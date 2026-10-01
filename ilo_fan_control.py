"""iLO4 Fan Profile Manager - standalone desktop GUI (tkinter + paramiko).
Run:  pip install paramiko   then   python ilo_fan_control.py
Build exe:  pip install pyinstaller  &&  pyinstaller --onefile --noconsole ilo_fan_control.py
"""
import json, math, os, sys, threading, time, tkinter as tk
from tkinter import ttk, messagebox
import paramiko

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "ilo_fan_config.json")
DEFAULT = {
    "host": "192.168.1.100", "user": "Administrator", "password": "", "port": 22,
    "fans": 6, "refresh_seconds": 5, "apply_delay": 8, "log_polling": 0, "info_first_id": 1, "scale": 255, "cfg_v": 3,
    # Command templates: {n}=fan number, {v}=value, {x}=fan id for the info command. Edit if your firmware differs.
    "cmd_info": "show system1/fan{x}", "cmd_min": "fan p {n} min {v}", "cmd_max": "fan p {n} max {v}",
    "profiles": [
        {"name": "Silent", "min": 10, "max": 30},
        {"name": "Balanced", "min": 20, "max": 50},
        {"name": "Performance", "min": 35, "max": 75},
        {"name": "Max", "min": 100, "max": 100},
    ],
    "last": 0,
}
BG, PANEL, FG, DIM, ACC = "#14171c", "#1d2128", "#e6e9ef", "#7d8590", "#3fb6ff"


def load_cfg():
    try:
        with open(CFG_PATH) as f:
            c = {**DEFAULT, **json.load(f)}
        if c.get("cfg_v", 1) < 2:  # migrate older saved configs
            c["cmd_info"] = DEFAULT["cmd_info"]
        c["cfg_v"] = 3
        return c
    except Exception:
        return json.loads(json.dumps(DEFAULT))


def save_cfg(c):
    with open(CFG_PATH, "w") as f:
        json.dump(c, f, indent=2)


def enable_legacy_ssh():
    """iLO4 only offers old SHA1 kex / ssh-dss host keys; paramiko disables them by default."""
    T = paramiko.Transport
    if "diffie-hellman-group14-sha1" not in T._kex_info:
        raise RuntimeError('This paramiko version (%s) has no SHA1 key exchange, which iLO4 needs. '
                           'Run:  py -m pip install "paramiko==3.5.1"' % paramiko.__version__)
    for k in ("diffie-hellman-group14-sha1", "diffie-hellman-group1-sha1", "diffie-hellman-group-exchange-sha1"):
        if k in getattr(T, "_kex_info", {}) and k not in T._preferred_kex:
            T._preferred_kex = tuple(T._preferred_kex) + (k,)
    for k in ("ssh-rsa", "ssh-dss"):
        if k in getattr(T, "_key_info", {}) and k not in T._preferred_keys:
            T._preferred_keys = tuple(T._preferred_keys) + (k,)


class Session:
    """Persistent SSH connection shared by live polling and profile applying."""
    def __init__(self):
        self.cl, self.sig, self.lock = None, None, threading.Lock()

    def close(self):
        try:
            if self.cl: self.cl.close()
        except Exception: pass
        self.cl = None

    def run(self, cfg, commands, log=None, blocking=True):
        """Returns list of (cmd, output), or None if non-blocking and the session is busy."""
        if not self.lock.acquire(blocking): return None
        log = log or (lambda s: None)
        try:
            sig = (cfg["host"], int(cfg["port"]), cfg["user"], cfg["password"])
            t = self.cl.get_transport() if self.cl else None
            if self.cl and (sig != self.sig or not t or not t.is_active()): self.close()
            if not self.cl:
                enable_legacy_ssh()
                cl = paramiko.SSHClient(); cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                log(f"SSH connecting {sig[2]}@{sig[0]}:{sig[1]} ...")
                cl.connect(sig[0], port=sig[1], username=sig[2], password=sig[3], timeout=8,
                           look_for_keys=False, allow_agent=False)
                self.cl, self.sig = cl, sig; log("SSH connected")
            out = []
            try:
                for c in commands:
                    log(f"$ {c}")
                    _, so, se = self.cl.exec_command(c, timeout=15)
                    text = (so.read() + se.read()).decode(errors="replace").strip()
                    code = so.channel.recv_exit_status()
                    log(f"{text or '(no output)'}  [exit {code}]")
                    out.append((c, text))
            except Exception:
                self.close(); raise
            return out
        finally:
            self.lock.release()


def ssh_run(cfg, commands, log=None):
    """One-shot connection (used by Test connection)."""
    s = Session()
    try: return s.run(cfg, commands, log)
    finally: s.close()


def parse_props(text):
    d = {}
    for line in text.splitlines():
        k, sep, v = line.strip().partition("=")
        if sep: d[k.strip()] = v.strip()
    return d


def fan_reading(props):
    for k, v in props.items():
        if "speed" in k.lower() or "rpm" in k.lower():
            try: return k, float(v.split()[0])
            except (ValueError, IndexError): pass
    return None, None


class Dial(tk.Canvas):
    """Rotary-style selector: mouse wheel or drag to rotate, click centre to apply."""
    def __init__(self, master, on_change, on_press, size=240):
        super().__init__(master, width=size, height=size, bg=PANEL, highlightthickness=0)
        self.s, self.on_change, self.on_press = size, on_change, on_press
        self.names, self.idx = [], 0
        self.bind("<MouseWheel>", lambda e: self.step(-1 if e.delta > 0 else 1))
        self.bind("<Button-4>", lambda e: self.step(-1)); self.bind("<Button-5>", lambda e: self.step(1))
        self.bind("<B1-Motion>", self.drag); self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Button-1>", self.press); self._moved = False

    def set(self, names, idx):
        self.names, self.idx = names, max(0, min(idx, len(names) - 1)); self.draw()

    def step(self, d):
        if self.names:
            self.idx = (self.idx + d) % len(self.names); self.draw(); self.on_change(self.idx)

    def angle(self, i):
        n = max(len(self.names), 1)
        return math.radians(-225 + 270 * (i / max(n - 1, 1)))

    def press(self, e): self._moved = False

    def drag(self, e):
        c = self.s / 2
        a = (math.degrees(math.atan2(e.y - c, e.x - c)) + 225) % 360
        if a > 270: a = 0 if a < 315 else 270
        n = len(self.names)
        i = round(a / 270 * (n - 1)) if n > 1 else 0
        if i != self.idx: self.idx = i; self._moved = True; self.draw(); self.on_change(i)

    def release(self, e):
        c = self.s / 2
        if not self._moved and math.hypot(e.x - c, e.y - c) < 55: self.on_press()

    def draw(self):
        self.delete("all"); c = self.s / 2; r = c - 24
        self.create_oval(c - r, c - r, c + r, c + r, outline="#2c333d", width=10)
        for i, nm in enumerate(self.names):
            a = self.angle(i); x, y = c + (r + 14) * math.cos(a), c + (r + 14) * math.sin(a)
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ACC if i == self.idx else "#3a424d", outline="")
        a = self.angle(self.idx)
        self.create_oval(c - r + 14, c - r + 14, c + r - 14, c + r - 14, fill="#262c35", outline="#3a424d", width=2)
        self.create_line(c + 20 * math.cos(a), c + 20 * math.sin(a), c + (r - 18) * math.cos(a),
                         c + (r - 18) * math.sin(a), fill=ACC, width=5, capstyle="round")
        if self.names:
            self.create_text(c, c + 4, text=self.names[self.idx], fill=FG, font=("Segoe UI", 12, "bold"))
            self.create_text(c, c + 24, text="click to apply", fill=DIM, font=("Segoe UI", 8))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("iLO4 Fan Control"); self.configure(bg=BG); self.geometry("900x700"); self.minsize(820, 640)
        self.cfg = load_cfg(); self.active = None; self.sess = Session(); self.applying = False
        self.pause_until = 0; self._last_err = None
        st = ttk.Style(self); st.theme_use("clam")
        st.configure("TButton", background="#2a313b", foreground=FG, borderwidth=0, padding=8)
        st.map("TButton", background=[("active", "#38414d")])
        st.configure("Accent.TButton", background=ACC, foreground="#06121c", font=("Segoe UI", 10, "bold"))
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, borderwidth=0, rowheight=26)
        st.configure("Treeview.Heading", background="#262c35", foreground=DIM, borderwidth=0)
        st.map("Treeview", background=[("selected", "#2b4a63")])
        self.build(); self.refresh_profiles(); self.build_fans()
        threading.Thread(target=self.poll_thread, daemon=True).start()

    def build(self):
        left = tk.Frame(self, bg=PANEL); left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        self.status = tk.Label(left, text="● not connected", bg=PANEL, fg=DIM, font=("Segoe UI", 9))
        self.status.pack(pady=(12, 0))
        self.dial = Dial(left, self.on_dial, self.apply); self.dial.pack(padx=20, pady=8)
        self.lbl = tk.Label(left, text="", bg=PANEL, fg=DIM, font=("Consolas", 10)); self.lbl.pack()
        ttk.Button(left, text="Apply profile", style="Accent.TButton", command=self.apply).pack(fill="x", padx=20, pady=(12, 4))
        ttk.Button(left, text="Connection settings", command=self.settings).pack(fill="x", padx=20, pady=(0, 4))
        ttk.Button(left, text="Restart iLO", command=self.restart_ilo).pack(fill="x", padx=20, pady=(0, 16))

        right = tk.Frame(self, bg=BG); right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        bar = tk.Frame(right, bg=BG); bar.pack(fill="x")
        tk.Label(bar, text="Profiles", bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).pack(side="left")
        for t, f in (("Delete", self.del_profile), ("Edit", self.edit_profile), ("Add", self.add_profile)):
            ttk.Button(bar, text=t, command=f).pack(side="right", padx=2)
        self.tree = ttk.Treeview(right, columns=("min", "max"), height=4)
        self.tree.heading("#0", text="Name", anchor="w"); self.tree.heading("min", text="Min %"); self.tree.heading("max", text="Max %")
        self.tree.column("min", width=70, anchor="center"); self.tree.column("max", width=70, anchor="center")
        self.tree.pack(fill="x", pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree); self.tree.bind("<Double-1>", lambda e: self.apply())
        tk.Label(right, text="Live fans", bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.fan_frame = tk.Frame(right, bg=BG); self.fan_frame.pack(fill="x", pady=6)
        bar2 = tk.Frame(right, bg=BG); bar2.pack(fill="x")
        tk.Label(bar2, text="Log", bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(bar2, text="Refresh now", command=self.refresh_now).pack(side="right")
        self.log = tk.Text(right, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, pady=8)

    # ---- helpers ----
    def say(self, text):
        """Thread-safe: prints to the console immediately and appends to the GUI log."""
        line = time.strftime("[%H:%M:%S] ") + text
        try: print(line, flush=True)
        except Exception: pass
        self.after(0, lambda: self._append(line))

    def _append(self, line):
        self.log.config(state="normal"); self.log.insert("end", line + "\n")
        self.log.see("end"); self.log.config(state="disabled")

    def build_fans(self):
        for w in self.fan_frame.winfo_children(): w.destroy()
        self.fan_widgets = []
        for n in range(int(self.cfg["fans"])):
            card = tk.Frame(self.fan_frame, bg=PANEL, padx=8, pady=6)
            card.grid(row=n // 3, column=n % 3, padx=3, pady=3, sticky="ew")
            self.fan_frame.grid_columnconfigure(n % 3, weight=1, uniform="f")
            name = tk.Label(card, text=f"Fan {int(self.cfg['info_first_id']) + n}", bg=PANEL, fg=DIM, font=("Segoe UI", 8)); name.pack(anchor="w")
            val = tk.Label(card, text="—", bg=PANEL, fg=FG, font=("Segoe UI", 15, "bold")); val.pack(anchor="w")
            bar = tk.Canvas(card, height=5, bg="#2c333d", highlightthickness=0); bar.pack(fill="x", pady=(2, 0))
            self.fan_widgets.append((name, val, bar))

    def update_fans(self, results):
        self._last_err = None
        for n, ((name, val, bar), (_, out)) in enumerate(zip(self.fan_widgets, results)):
            props = parse_props(out); key, num = fan_reading(props)
            status = props.get("OperationalStatus") or props.get("HealthState") or ""
            name.config(text=f"Fan {int(self.cfg['info_first_id']) + n}" + (f"  ·  {status}" if status else ""))
            val.config(text=f"{num:g}" if num is not None else "?")
            frac = 0 if num is None else min(num / 100 if num <= 100 else num / 255 if num <= 255 else 1, 1)
            bar.delete("all"); bar.update_idletasks()
            bar.create_rectangle(0, 0, bar.winfo_width() * frac, 5, fill=ACC, outline="")
        self.status.config(text=f"● live  {time.strftime('%H:%M:%S')}", fg="#4cd964")

    def info_cmds(self):
        c = self.cfg; first = int(c.get("info_first_id", 1))
        return [c["cmd_info"].format(x=first + n, n=n) for n in range(int(c["fans"]))]

    def refresh_profiles(self):
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.cfg["profiles"]):
            self.tree.insert("", "end", iid=str(i), text=("▶ " if i == self.active else "  ") + p["name"], values=(p["min"], p["max"]))
        self.sel = min(self.cfg.get("last", 0), len(self.cfg["profiles"]) - 1)
        self.dial.set([p["name"] for p in self.cfg["profiles"]], self.sel); self.on_dial(self.sel)

    def on_dial(self, i):
        self.sel = i; p = self.cfg["profiles"][i]
        self.lbl.config(text=f"min {p['min']}%   max {p['max']}%")
        if self.tree.exists(str(i)) and self.tree.selection() != (str(i),):
            self.tree.selection_set(str(i))

    def on_tree(self, _):
        s = self.tree.selection()
        if s and int(s[0]) != self.dial.idx:
            self.sel = int(s[0]); self.dial.idx = self.sel; self.dial.draw(); self.on_dial(self.sel)

    def fail(self, e):
        self.status.config(text="● error", fg="#ff6b6b")
        msg = str(e) or e.__class__.__name__
        if msg != self._last_err: self.say(f"ERROR: {msg}")
        self._last_err = msg

    # ---- live polling ----
    def poll_thread(self):
        while True:
            c = self.cfg
            every = int(c.get("refresh_seconds", 5))
            if every <= 0 or not c["host"] or not c["password"] or self.applying:
                time.sleep(0.5); continue
            left = self.pause_until - time.time()
            if left > 0:
                self.after(0, lambda s=left: self.status.config(text=f"● settling ... {s:.0f}s", fg="#ffb347"))
                time.sleep(min(left, 1)); continue
            self.poll_once()
            time.sleep(max(2, every))

    def poll_once(self, verbose=False):
        c = self.cfg
        try:
            res = self.sess.run(c, self.info_cmds(), self.say if (verbose or int(c.get("log_polling", 0))) else None, blocking=verbose)
            if res is not None: self.after(0, lambda r=res: self.update_fans(r))
        except Exception as e:
            self.after(0, lambda err=e: self.fail(err))

    def refresh_now(self):
        threading.Thread(target=lambda: self.poll_once(True), daemon=True).start()

    # ---- actions ----
    def apply(self, idx=None):
        if self.applying:
            self.say("Busy - please wait."); return
        idx = self.sel if not isinstance(idx, int) else idx
        p = self.cfg["profiles"][idx]; c = self.cfg
        scale = float(c.get("scale", 255)); delay = float(c.get("apply_delay", 8))
        v = lambda pct: int(round(pct * scale / 100))
        cmds = []
        for n in range(int(c["fans"])):  # max, min, max: works whether the range is widening or narrowing
            cmds += [c["cmd_max"].format(n=n, v=v(p["max"])), c["cmd_min"].format(n=n, v=v(p["min"])),
                     c["cmd_max"].format(n=n, v=v(p["max"]))]
        self.say(f"Applying '{p['name']}' (100% = {scale:g}) ...")
        self.applying = True
        def job():
            try:
                res = self.sess.run(c, cmds, self.say)
                words = ("error", "invalid", "unknown", "unsupported", "not found", "usage")
                bad = [o for _, o in res if o and any(w in o.lower() for w in words)]
                def done():
                    self.active = idx; c["last"] = idx; save_cfg(c); self.refresh_profiles()
                    self.status.config(text=f"● {p['name']} sent", fg="#4cd964" if not bad else "#ffb347")
                    self.say("Done." + (f" Possible problem: {bad[0][:100]}" if bad else "") +
                             f" Reading fan speeds again in {delay:g}s.")
                self.after(0, done)
            except Exception as e:
                self.after(0, lambda err=e: self.fail(err))
            finally:
                self.pause_until = time.time() + delay
                self.applying = False
        threading.Thread(target=job, daemon=True).start()

    def restart_ilo(self):
        if self.applying: self.say("Busy - please wait."); return
        again = self.active
        if not messagebox.askyesno("Restart iLO", "Restart the iLO management processor (reset map1)?\n\n"
                "The server and its OS keep running. iLO is unreachable for 1-2 minutes."
                + ("\nThe active profile will be re-applied afterwards." if again is not None else "")):
            return
        c = self.cfg; self.applying = True
        def status(t, col="#ffb347"): self.after(0, lambda: self.status.config(text=t, fg=col))
        def job():
            back = False
            try:
                self.say("Restarting iLO: reset map1")
                status("● iLO restarting ...")
                try: self.sess.run(c, ["reset map1"], self.say)
                except Exception as e: self.say(f"Connection closed ({e.__class__.__name__}) - expected during reset")
                self.sess.close()
                t0 = time.time(); time.sleep(20)
                while time.time() - t0 < 240 and not back:
                    status(f"● iLO restarting ... {time.time() - t0:.0f}s")
                    try: self.sess.run(c, [self.info_cmds()[0]], None); back = True
                    except Exception: self.sess.close(); time.sleep(5)
                self.say("iLO is back online." if back else "iLO did not come back within 4 minutes - check it manually.")
                if not back: status("● iLO unreachable", "#ff6b6b")
            finally:
                self.pause_until = time.time() + (5 if back else 0)
                self.applying = False
            if back and again is not None:
                self.say(f"Re-applying profile '{c['profiles'][again]['name']}' ...")
                self.after(0, lambda: self.apply(again))
        threading.Thread(target=job, daemon=True).start()

    # ---- dialogs ----
    def form(self, title, fields, values, on_ok, extra=()):
        w = tk.Toplevel(self); w.title(title); w.configure(bg=PANEL); w.transient(self); w.grab_set()
        ents = {}
        for r, (key, label, secret) in enumerate(fields):
            tk.Label(w, text=label, bg=PANEL, fg=FG).grid(row=r, column=0, sticky="w", padx=12, pady=5)
            e = tk.Entry(w, bg=BG, fg=FG, insertbackground=FG, relief="flat", width=34, show="*" if secret else "")
            e.insert(0, str(values.get(key, ""))); e.grid(row=r, column=1, padx=12, pady=5); ents[key] = e
        def ok():
            try: on_ok({k: e.get() for k, e in ents.items()}); w.destroy()
            except Exception as ex: messagebox.showerror("Invalid", str(ex), parent=w)
        r = len(fields)
        msg = tk.Label(w, text="", bg=PANEL, fg=DIM, wraplength=420, justify="left")
        msg.grid(row=r + 1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))
        def setmsg(t, col=DIM):
            try: msg.config(text=t, fg=col)
            except tk.TclError: pass
        for label, fn in extra:
            ttk.Button(w, text=label, command=lambda fn=fn: fn({k: e.get() for k, e in ents.items()}, setmsg)).grid(row=r, column=0, padx=12, pady=10, sticky="w")
        ttk.Button(w, text="Save", style="Accent.TButton", command=ok).grid(row=r, column=1, sticky="e", padx=12, pady=10)

    def settings(self):
        f = [("host", "iLO host/IP", 0), ("port", "SSH port", 0), ("user", "Username", 0), ("password", "Password", 1),
             ("fans", "Number of fans", 0), ("refresh_seconds", "Fan speed refresh interval s (0=off)", 0),
             ("apply_delay", "Wait after apply before reading (s)", 0), ("log_polling", "Log refreshes to console (0/1)", 0),
             ("cmd_info", "Info cmd ({x} = fan id)", 0), ("info_first_id", "First fan id for info", 0),
             ("cmd_min", "Min command", 0), ("cmd_max", "Max command", 0), ("scale", "Value for 100% (255 or 100)", 0)]
        def ok(v):
            for k in ("port", "fans", "refresh_seconds", "info_first_id", "log_polling"): v[k] = int(v[k])
            v["scale"] = float(v["scale"]); v["apply_delay"] = max(0.0, float(v["apply_delay"]))
            if 0 < v["refresh_seconds"] < 2: v["refresh_seconds"] = 2
            self.cfg.update(v); save_cfg(self.cfg); self.build_fans(); self.say("Settings saved.")
        self.form("Connection settings", f, self.cfg, ok, extra=[("Test connection", self.test_conn)])

    def test_conn(self, v, setmsg):
        try:
            cfg = {**self.cfg, "host": v["host"], "port": int(v["port"]), "user": v["user"], "password": v["password"]}
            cmd = v["cmd_info"].format(x=int(v["info_first_id"]), n=0)
        except Exception as e:
            setmsg(f"Invalid settings: {e}", "#ff6b6b"); return
        setmsg("Testing ...")
        def job():
            try:
                out = ssh_run(cfg, [cmd], self.say)[0][1]
                m, col = "Connected OK. " + (out.splitlines()[0][:70] if out else "(command returned no output)"), "#4cd964"
            except Exception as e:
                m, col = f"Failed: {e}", "#ff6b6b"
            self.after(0, lambda: setmsg(m, col))
        threading.Thread(target=job, daemon=True).start()

    def prof_form(self, title, vals, setter):
        def ok(v):
            p = {"name": v["name"].strip(), "min": int(v["min"]), "max": int(v["max"])}
            if not p["name"] or not (0 <= p["min"] <= p["max"] <= 100): raise ValueError("Name required; 0 <= min <= max <= 100")
            setter(p); save_cfg(self.cfg); self.refresh_profiles()
        self.form(title, [("name", "Name", 0), ("min", "Min %", 0), ("max", "Max %", 0)], vals, ok)

    def add_profile(self):
        self.prof_form("Add profile", {"min": 20, "max": 60}, lambda p: self.cfg["profiles"].append(p))

    def edit_profile(self):
        i = self.sel; self.prof_form("Edit profile", self.cfg["profiles"][i], lambda p: self.cfg["profiles"].__setitem__(i, p))

    def del_profile(self):
        if len(self.cfg["profiles"]) > 1 and messagebox.askyesno("Delete", "Delete this profile?"):
            self.cfg["profiles"].pop(self.sel); self.cfg["last"] = 0; save_cfg(self.cfg); self.refresh_profiles()


if __name__ == "__main__":
    print("iLO4 Fan Control - live log (everything below is also shown in the GUI)", flush=True)
    App().mainloop()
