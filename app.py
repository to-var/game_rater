"""
Desktop GUI for game_rater. Wraps fetch_games.py, scrape_metadata.py, and
delete_flagged.py behind a Tkinter UI, backed by the SQLite DB in db.py:

  - New Scan: pick a roms folder, scan it into a session.
  - Open Previous Session: pick from previously scanned roms folders and
    continue from there (remove flags / rating / language persist in the DB).
  - Browse each platform's games in a table, toggle "remove" per game
    (written straight to the DB, no separate save step).
  - Scrape: fill in rating/language via RAWG.io (API key persists in
    ~/.game_rater/config.json, editable from the Settings menu). Usage
    against the monthly allowance is tracked there too.

Run with:
    python app.py
"""

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import config
import db
import fetch_games
import scrape_metadata


class SessionPickerDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Open Previous Session")
        self.geometry("640x300")
        self.result_session_id: int | None = None

        columns = ("roms_dir", "games", "last_scanned")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("roms_dir", text="Roms Folder")
        self.tree.heading("games", text="Games")
        self.tree.heading("last_scanned", text="Last Scanned")
        self.tree.column("roms_dir", width=380, anchor="w")
        self.tree.column("games", width=80, anchor="center")
        self.tree.column("last_scanned", width=160, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree.bind("<Double-1>", lambda _e: self._on_ok())

        for session in db.list_sessions():
            self.tree.insert("", "end", iid=str(session["id"]), values=(
                session["roms_dir"], session["game_count"], session["last_scanned_at"],
            ))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Open", command=self._on_ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))

        self.transient(parent)
        self.grab_set()

    def _on_ok(self):
        selected = self.tree.selection()
        if not selected:
            return
        self.result_session_id = int(selected[0])
        self.destroy()


class GameRaterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Game Rater")
        self.root.geometry("900x620")

        self.session_id: int | None = None
        self.roms_dir: Path | None = None
        self.current_platform: str | None = None
        self.log_queue: queue.Queue = queue.Queue()

        self._build_menu()
        self._build_ui()
        self.root.after(100, self._drain_log_queue)

        self._update_api_key_warning()
        self._resume_latest_session()

    # ---------- menu ----------

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Scan...", command=self._on_new_scan)
        file_menu.add_command(label="Open Previous Session...", command=self._on_open_session)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Set RAWG API Key...", command=self._on_set_api_key)
        settings_menu.add_command(label="View API Usage...", command=self._on_view_usage)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        self.root.config(menu=menubar)

    def _on_set_api_key(self):
        current = config.get_api_key() or ""
        new_key = simpledialog.askstring(
            "RAWG API Key",
            "Enter your RAWG API key\n(https://rawg.io/apidocs):",
            initialvalue=current,
            show="*",
        )
        if new_key:
            config.set_api_key(new_key.strip())
            self._log("API key saved.")
            self._update_api_key_warning()

    def _on_view_usage(self):
        count, allowance = config.get_usage()
        messagebox.showinfo(
            "RAWG API Usage",
            f"Calls used this month: {count} / {allowance}\n"
            f"Remaining: {max(allowance - count, 0)}",
        )

    # ---------- UI construction ----------

    def _build_ui(self):
        self.warning_label = ttk.Label(
            self.root, text="", foreground="#b00000", padding=(8, 4),
        )
        self.warning_label.pack(fill="x")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="New Scan...", command=self._on_new_scan).pack(side="left")
        ttk.Button(top, text="Open Previous Session...", command=self._on_open_session).pack(side="left", padx=8)
        self.session_label = ttk.Label(top, text="No session open")
        self.session_label.pack(side="left", padx=8)

        actions = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        actions.pack(fill="x")

        self.scrape_btn = ttk.Button(actions, text="Scrape Metadata (selected platform)", command=self._on_scrape, state="disabled")
        self.scrape_btn.pack(side="left")

        self.delete_btn = ttk.Button(actions, text="Delete Flagged Files (this session)...", command=self._on_delete, state="disabled")
        self.delete_btn.pack(side="left", padx=8)

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(body, width=180)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Platforms").pack(anchor="w")
        self.platform_list = tk.Listbox(left, width=20)
        self.platform_list.pack(fill="y", expand=True)
        self.platform_list.bind("<<ListboxSelect>>", self._on_platform_selected)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        columns = ("name", "rating", "language", "remove")
        self.tree = ttk.Treeview(right, columns=columns, show="headings", selectmode="browse")
        for col, width in (("name", 380), ("rating", 70), ("language", 120), ("remove", 80)):
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_row_double_click)

        ttk.Label(right, text="Double-click a row to toggle its remove flag (saved immediately).").pack(anchor="w", pady=(4, 0))

        log_frame = ttk.LabelFrame(self.root, text="Log", padding=4)
        log_frame.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=8, state="disabled")
        self.log_text.pack(fill="both", expand=True)

    def _update_api_key_warning(self):
        if config.get_api_key() or os.environ.get("RAWG_API_KEY"):
            self.warning_label.configure(text="")
        else:
            self.warning_label.configure(
                text="⚠ RAWG API key not set — set it via Settings → Set RAWG API Key..."
            )

    # ---------- logging ----------

    def _log(self, msg: str):
        self.log_queue.put(msg)

    def _drain_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(100, self._drain_log_queue)

    # ---------- session management ----------

    def _resume_latest_session(self):
        latest = db.get_latest_session()
        if latest:
            self._open_session(latest["id"])

    def _on_new_scan(self):
        chosen = filedialog.askdirectory(title="Select your Roms folder")
        if not chosen:
            return
        roms_dir = Path(chosen)
        threading.Thread(target=self._scan_worker, args=(roms_dir,), daemon=True).start()

    def _scan_worker(self, roms_dir: Path):
        try:
            session_id = fetch_games.fetch_roms_dir(roms_dir)
            self._log(f"Scan complete: {roms_dir}")
        except Exception as e:
            self._log(f"Scan failed: {e}")
            return
        self.root.after(0, self._open_session, session_id)

    def _on_open_session(self):
        dialog = SessionPickerDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result_session_id is not None:
            self._open_session(dialog.result_session_id)

    def _open_session(self, session_id: int):
        session = db.get_session(session_id)
        if not session:
            return
        self.session_id = session_id
        self.roms_dir = Path(session["roms_dir"])
        self.session_label.configure(text=f"Session: {self.roms_dir} (id {session_id})")
        self.scrape_btn.configure(state="normal")
        self.delete_btn.configure(state="normal")
        self._refresh_platform_list()

    def _refresh_platform_list(self):
        self.platform_list.delete(0, "end")
        if self.session_id is None:
            return
        for platform in db.get_platforms(self.session_id):
            self.platform_list.insert("end", platform)

    # ---------- platform table ----------

    def _on_platform_selected(self, _event):
        selection = self.platform_list.curselection()
        if not selection:
            return
        platform = self.platform_list.get(selection[0])
        self.current_platform = platform
        self._load_platform_table(platform)

    def _load_platform_table(self, platform: str):
        games = db.get_games(self.session_id, platform)
        self.tree.delete(*self.tree.get_children())
        for game in games:
            self.tree.insert("", "end", iid=str(game["id"]), values=(
                game["name"],
                game["rating"] if game["rating"] is not None else "",
                game["language"] or "",
                "YES" if game["remove_flag"] else "no",
            ))

    def _on_row_double_click(self, _event):
        selected = self.tree.selection()
        if not selected:
            return
        game_id = int(selected[0])
        current_value = self.tree.set(game_id, "remove")
        new_remove = current_value != "YES"
        db.set_remove_flag(game_id, new_remove)
        self.tree.set(game_id, "remove", "YES" if new_remove else "no")

    # ---------- scrape ----------

    def _on_scrape(self):
        if not self.current_platform or self.session_id is None:
            messagebox.showinfo("Scrape Metadata", "Select a platform first.")
            return

        is_arcade = self.current_platform in scrape_metadata.ARCADE_PLATFORMS
        api_key = os.environ.get("RAWG_API_KEY") or config.get_api_key()
        if not api_key and not is_arcade:
            api_key = simpledialog.askstring(
                "RAWG API Key",
                "No API key saved yet.\nEnter your free RAWG API key\n(https://rawg.io/apidocs):",
                show="*",
            )
            if not api_key:
                return
            api_key = api_key.strip()
            config.set_api_key(api_key)
            self._update_api_key_warning()

        if not is_arcade:
            count, allowance = config.get_usage()
            if count >= allowance:
                proceed = messagebox.askyesno(
                    "Monthly Allowance Reached",
                    f"You've used {count}/{allowance} RAWG calls this month.\n"
                    "Continue anyway?",
                )
                if not proceed:
                    return

        self.scrape_btn.configure(state="disabled")
        threading.Thread(
            target=self._scrape_worker, args=(self.session_id, self.current_platform, api_key), daemon=True
        ).start()

    def _scrape_worker(self, session_id: int, platform: str, api_key: str):
        try:
            scrape_metadata.scrape_platform(session_id, platform, api_key, log=self._log)
            self._log(f"Scrape complete for {platform}.")
        except Exception as e:
            self._log(f"Scrape failed: {e}")
        finally:
            self.root.after(0, self._after_scrape, platform)

    def _after_scrape(self, platform: str):
        self.scrape_btn.configure(state="normal")
        if platform == self.current_platform:
            self._load_platform_table(platform)

    # ---------- delete ----------

    def _on_delete(self):
        if self.session_id is None:
            return
        flagged = db.get_flagged_games(self.session_id)
        if not flagged:
            messagebox.showinfo("Delete Flagged Files", "Nothing is flagged for removal.")
            return

        preview = "\n".join(g["file_path"] for g in flagged[:20])
        more = f"\n... and {len(flagged) - 20} more" if len(flagged) > 20 else ""
        confirmed = messagebox.askyesno(
            "Delete Flagged Files",
            f"Permanently delete {len(flagged)} file(s)?\n\n{preview}{more}",
        )
        if not confirmed:
            return

        self.delete_btn.configure(state="disabled")
        threading.Thread(target=self._delete_worker, args=(self.session_id,), daemon=True).start()

    def _delete_worker(self, session_id: int):
        try:
            flagged = db.get_flagged_games(session_id)
            deleted_count = 0
            for game in flagged:
                file_path = Path(game["file_path"])
                try:
                    if file_path.exists():
                        file_path.unlink()
                        deleted_count += 1
                        self._log(f"Deleted: {file_path}")
                    else:
                        self._log(f"Already missing: {file_path}")
                    db.delete_game(game["id"])
                except OSError as e:
                    self._log(f"FAILED to delete {file_path}: {e}")
            self._log(f"Delete complete. {deleted_count} file(s) removed.")
        except Exception as e:
            self._log(f"Delete failed: {e}")
        finally:
            self.root.after(0, self._after_delete)

    def _after_delete(self):
        self.delete_btn.configure(state="normal")
        if self.current_platform:
            self._load_platform_table(self.current_platform)


def main():
    root = tk.Tk()
    GameRaterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
