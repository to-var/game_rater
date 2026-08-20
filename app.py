"""
Desktop GUI for game_rater. Wraps fetch_games.py, scrape_metadata.py, and
delete_flagged.py behind a Tkinter UI:

  - Pick a roms folder (and optionally a metadata folder).
  - Fetch: scan the roms folder into per-platform JSON.
  - Browse each platform's games in a table, toggle "remove" per game
    (saved to JSON immediately).
  - Scrape: fill in rating/language via TheGamesDB (asks for API key if
    TGDB_API_KEY isn't set).
  - Delete: remove every file flagged for removal, across all platforms.

Run with:
    python app.py
"""

import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import delete_flagged
import fetch_games
import scrape_metadata


class GameRaterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Game Rater")
        self.root.geometry("900x600")

        self.roms_dir: Path | None = None
        self.metadata_dir: Path | None = None
        self.current_platform: str | None = None
        self.entries: list[dict] = []
        self.log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    # ---------- UI construction ----------

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Pick Roms Folder...", command=self._pick_roms_dir).pack(side="left")
        self.roms_dir_label = ttk.Label(top, text="No roms folder selected")
        self.roms_dir_label.pack(side="left", padx=8)

        actions = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        actions.pack(fill="x")

        self.fetch_btn = ttk.Button(actions, text="Fetch Games", command=self._on_fetch, state="disabled")
        self.fetch_btn.pack(side="left")

        self.scrape_btn = ttk.Button(actions, text="Scrape Metadata (selected platform)", command=self._on_scrape, state="disabled")
        self.scrape_btn.pack(side="left", padx=8)

        self.delete_btn = ttk.Button(actions, text="Delete Flagged Files...", command=self._on_delete, state="disabled")
        self.delete_btn.pack(side="left")

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

        ttk.Label(right, text="Double-click a row (or its Remove cell) to toggle remove flag.").pack(anchor="w", pady=(4, 0))

        log_frame = ttk.LabelFrame(self.root, text="Log", padding=4)
        log_frame.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=8, state="disabled")
        self.log_text.pack(fill="both", expand=True)

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

    # ---------- folder selection ----------

    def _pick_roms_dir(self):
        chosen = filedialog.askdirectory(title="Select your Roms folder")
        if not chosen:
            return
        self.roms_dir = Path(chosen)
        self.metadata_dir = self.roms_dir.parent / "metadata"
        self.roms_dir_label.configure(text=f"{self.roms_dir}  (metadata: {self.metadata_dir})")
        self.fetch_btn.configure(state="normal")
        self._refresh_platform_list()

    def _refresh_platform_list(self):
        self.platform_list.delete(0, "end")
        if not self.metadata_dir or not self.metadata_dir.exists():
            return
        for json_path in sorted(self.metadata_dir.glob("*.json")):
            if json_path.stem.startswith("_"):
                continue
            self.platform_list.insert("end", json_path.stem)

    # ---------- fetch ----------

    def _on_fetch(self):
        if not self.roms_dir:
            return
        self.fetch_btn.configure(state="disabled")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            self.metadata_dir.mkdir(parents=True, exist_ok=True)
            platform_dirs = sorted(p for p in self.roms_dir.iterdir() if p.is_dir())
            for platform_dir in platform_dirs:
                entries = fetch_games.fetch_platform(platform_dir, self.metadata_dir)
                json_path = self.metadata_dir / f"{platform_dir.name}.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(entries, f, indent=2, ensure_ascii=False)
                self._log(f"{platform_dir.name}: {len(entries)} entries")
            self._log("Fetch complete.")
        except Exception as e:
            self._log(f"Fetch failed: {e}")
        finally:
            self.root.after(0, self._after_fetch)

    def _after_fetch(self):
        self.fetch_btn.configure(state="normal")
        self.scrape_btn.configure(state="normal")
        self.delete_btn.configure(state="normal")
        self._refresh_platform_list()

    # ---------- platform table ----------

    def _on_platform_selected(self, _event):
        selection = self.platform_list.curselection()
        if not selection:
            return
        platform = self.platform_list.get(selection[0])
        self.current_platform = platform
        self._load_platform_table(platform)

    def _load_platform_table(self, platform: str):
        json_path = self.metadata_dir / f"{platform}.json"
        with open(json_path, "r", encoding="utf-8") as f:
            self.entries = json.load(f)

        self.tree.delete(*self.tree.get_children())
        for i, entry in enumerate(self.entries):
            self.tree.insert("", "end", iid=str(i), values=(
                entry["name"],
                entry.get("rating") if entry.get("rating") is not None else "",
                entry.get("language") or "",
                "YES" if entry.get("remove") else "no",
            ))

    def _on_row_double_click(self, _event):
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        index = int(iid)
        entry = self.entries[index]
        entry["remove"] = not entry.get("remove", False)
        self.tree.set(iid, "remove", "YES" if entry["remove"] else "no")
        self._save_current_platform()

    def _save_current_platform(self):
        if not self.current_platform:
            return
        json_path = self.metadata_dir / f"{self.current_platform}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)

    # ---------- scrape ----------

    def _on_scrape(self):
        if not self.current_platform:
            messagebox.showinfo("Scrape Metadata", "Select a platform first.")
            return

        api_key = os.environ.get("TGDB_API_KEY")
        if not api_key:
            api_key = simpledialog.askstring(
                "TheGamesDB API Key",
                "TGDB_API_KEY is not set.\nEnter your free TheGamesDB API key\n(https://thegamesdb.net/):",
                show="*",
            )
            if not api_key:
                return
            os.environ["TGDB_API_KEY"] = api_key

        self.scrape_btn.configure(state="disabled")
        threading.Thread(target=self._scrape_worker, args=(self.current_platform, api_key), daemon=True).start()

    def _scrape_worker(self, platform: str, api_key: str):
        try:
            platform_ids = scrape_metadata.load_platform_ids(api_key, self.metadata_dir)
            scrape_metadata.process_platform(platform, api_key, platform_ids, self.metadata_dir)
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
        flagged = []
        for json_path in sorted(self.metadata_dir.glob("*.json")):
            if json_path.stem.startswith("_"):
                continue
            with open(json_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            for entry in entries:
                if entry.get("remove"):
                    flagged.append(entry["file_path"])

        if not flagged:
            messagebox.showinfo("Delete Flagged Files", "Nothing is flagged for removal.")
            return

        preview = "\n".join(flagged[:20])
        more = f"\n... and {len(flagged) - 20} more" if len(flagged) > 20 else ""
        confirmed = messagebox.askyesno(
            "Delete Flagged Files",
            f"Permanently delete {len(flagged)} file(s)?\n\n{preview}{more}",
        )
        if not confirmed:
            return

        self.delete_btn.configure(state="disabled")
        threading.Thread(target=self._delete_worker, daemon=True).start()

    def _delete_worker(self):
        try:
            json_paths = sorted(self.metadata_dir.glob("*.json"))
            deleted_count = 0
            for json_path in json_paths:
                if json_path.stem.startswith("_"):
                    continue
                with open(json_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                remaining = []
                for entry in entries:
                    if entry.get("remove"):
                        file_path = Path(entry["file_path"])
                        try:
                            if file_path.exists():
                                file_path.unlink()
                                deleted_count += 1
                                self._log(f"Deleted: {file_path}")
                            else:
                                self._log(f"Already missing: {file_path}")
                        except OSError as e:
                            self._log(f"FAILED to delete {file_path}: {e}")
                            remaining.append(entry)
                    else:
                        remaining.append(entry)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(remaining, f, indent=2, ensure_ascii=False)
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
