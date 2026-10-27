"""Pictures for a stickman explainer, through Google Flow (gflow-cli / flow-auto). Run from the project folder.

    python <skill>/scripts/pictures.py refs <sheet.jpg> --names modern,caveman [--cuts 0.5]
        a character sheet -> img/sheet.jpg + one reference per character (img/ref_<name>.jpg)
    python <skill>/scripts/pictures.py submit 1 8          # test 2 shots first (look + characters)
    python <skill>/scripts/pictures.py submit              # every shot in SHOTS.tsv
    python <skill>/scripts/pictures.py submit --dry       # print the commands, generate nothing
    python <skill>/scripts/pictures.py review [from to]    # labelled grid of the pictures -> review.jpg
    python <skill>/scripts/pictures.py pick                # sync raw -> img/sNN.jpg (PICK.tsv fixes swaps)
    python <skill>/scripts/pictures.py qc                  # frames/ -> qc.jpg contact sheet after render
    python <skill>/scripts/pictures.py elements [ids]      # ELEMENTS.tsv -> cut-out objects on magenta (1:1)
    python <skill>/scripts/pictures.py cutouts             # elements/raw -> elements/cut/NN.png (magenta keyed off)

ELEMENTS.tsv  id, ref, object   one object or one character pose per row, for the hybrid look (PLAYBOOK §9).
              Generates to elements/raw/NN.png; cutouts keys off magenta to elements/cut/NN.png.

SHOTS.tsv  id, ref, prompt[, space]   ref = - (no character) | a name from refs (img/ref_<name>.jpg) | sheet
           space = right | left | top | - : that 40% of the picture stays empty and render.py puts stickers
           there; MIX the sides across a film so frames do not all look alike
           flip = yes: render.py mirrors the picture (after LaMa), e.g. a right-empty picture used as left
STYLE.txt  line 1 "look: <name>", then the style paragraph appended to every prompt (from looks/<name>.md,
           adapted to the topic)
PICK.tsv   shot, file   optional swap override if needed.

CLI Engine: uses gflow (Google Flow CLI) by default, or flow-auto as fallback.
Aspect ratio: STICK_ASPECT env var (default 16:9; supports 9:16 for vertical Shorts/Reels).
Model: STICK_MODEL (default nano2 in gflow, nano-banana-2 in flow-auto).
"""
import csv, glob, hashlib, json, os, re, shutil, subprocess, sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import unmark  # noqa: E402

FIX = (" The attached image is only a character design reference: keep the same face, hair, proportions and "
       "line style (change clothes only if this prompt says so) and draw each person only once, inside the "
       "scene. Ignore the reference's backdrop and colour tone, and do not copy its line-up.")
# Default models: nano2 in gflow, nano-banana-2 in flow-auto
MODEL = os.environ.get("STICK_MODEL", "nano2")
ASPECT = os.environ.get("STICK_ASPECT", "16:9")
NO_TEXT = " Absolutely no text, no letters, no numbers, no labels, no watermark."


def gflow_exe():
    exe = shutil.which("gflow") or shutil.which("gflow-cli")
    if not exe:
        candidates = [
            os.path.expanduser("~/.local/bin/gflow.exe"),
            os.path.expanduser("~/.local/bin/gflow-cli.exe"),
            os.path.expanduser("~/.local/bin/gflow"),
            os.path.expanduser("~/.local/bin/gflow-cli"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "gflow", "gflow.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    return exe


def get_or_create_project(exe):
    proj_file = ".flow_project"
    if os.path.exists(proj_file):
        pid = open(proj_file, "r", encoding="utf-8").read().strip()
        if pid:
            return pid
    title = folder()
    res = subprocess.run([exe, "project", "create", "--name", title, "--json"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(res.stdout)
        pid = data.get("project_id")
        if pid:
            with open(proj_file, "w", encoding="utf-8") as f:
                f.write(pid)
            return pid
    except Exception:
        pass
    return None


def folder():
    return "stick_" + re.sub(r"[^a-z0-9_-]", "", os.path.basename(os.getcwd()).lower())


def downloads():
    d = os.environ.get("FLOW_DOWNLOADS")
    if d:
        return d
    root = os.path.join(os.path.expanduser("~"), "Downloads")
    for depth in range(4):
        hit = glob.glob(os.path.join(root, *(["*"] * depth), folder()))
        if hit:
            return hit[0]
    return None


def ids(args):
    out = []
    for a in args:
        if "-" in a:
            x, y = a.split("-")
            out += range(int(x), int(y) + 1)
        else:
            out.append(int(a))
    return set(out)


def shots():
    return list(csv.DictReader(open("SHOTS.tsv", encoding="utf-8"), delimiter="\t"))


def style():
    lines = open("STYLE.txt", encoding="utf-8").read().splitlines()
    body = " ".join(l for l in lines if l.strip() and not l.lower().startswith("look:"))
    return " Style: " + body.strip() + NO_TEXT


def refs(a):
    sheet = a[0]
    names = a[a.index("--names") + 1].split(",")
    cuts = [float(c) for c in a[a.index("--cuts") + 1].split(",")] if "--cuts" in a else \
        [i / len(names) for i in range(1, len(names))]
    os.makedirs("img", exist_ok=True)
    im = unmark(Image.open(sheet))
    im.save("img/sheet.jpg", quality=95)
    xs = [0] + [int(c * im.width) for c in cuts] + [im.width]
    for n, x0, x1 in zip(names, xs, xs[1:]):
        im.crop((x0, 0, x1, im.height)).save(f"img/ref_{n}.jpg", quality=95)
    print("wrote img/sheet.jpg and", ", ".join(f"img/ref_{n}.jpg" for n in names), "- look at each one")


def layout_check():
    """Warn when the space column repeats: 3 of the same side in a row, or one side on over 40% of shots.
    Layouts are chosen per shot from its line, so every film gets its own mix (PLAYBOOK section 9)."""
    sides = [((r.get("space") or "").strip().lower() or "-") for r in shots()]
    warn = []
    for i in range(2, len(sides)):
        if sides[i] == sides[i - 1] == sides[i - 2]:
            warn.append(f"shots {i - 1}-{i + 1} are all '{sides[i]}'")
    for side in set(sides):
        if len(sides) >= 8 and sides.count(side) / len(sides) > 0.4:
            warn.append(f"'{side}' is used on {sides.count(side)} of {len(sides)} shots")
    print("layout:", " ".join(sides))
    for w in warn:
        print("LAYOUT WARNING:", w, "- vary it by what each line needs")


def submit(a):
    exe = gflow_exe()
    if not exe:
        exe = shutil.which("flow-auto") or sys.exit("gflow-cli (or flow-auto) is not installed.")
    is_gflow = "gflow" in os.path.basename(exe).lower()

    want = ids([x for x in a if not x.startswith("--")])
    layout_check()
    st = style()
    os.makedirs("img/raw", exist_ok=True)
    os.makedirs("img", exist_ok=True)

    pid = get_or_create_project(exe) if is_gflow else None
    model_name = MODEL if is_gflow else ("nano-banana-2" if MODEL == "nano2" else MODEL)

    n = 0
    for r in shots():
        i = int(r["id"])
        if want and i not in want:
            continue
        ref, prompt = r["ref"].strip(), r["prompt"].strip()
        side = (r.get("space") or "").strip().lower()
        if side in ("left", "right"):
            keep = "left" if side == "right" else "right"
            prompt += (f" Composition: a complete, natural, full scene that fills the whole frame, with the main "
                       f"character and the main subject placed slightly towards the {keep}, and a calmer, less busy "
                       f"area on the {side} side (open sky, plain wall, ground or water) without important details. "
                       f"The scene must still look finished and rich, not empty.")
        elif side == "top":
            prompt += (" Composition: a complete, natural, full scene that fills the whole frame, with the main "
                       "character and the main subject in the lower part and a calm open sky or plain upper wall above "
                       "them without important details. The scene must still look finished and rich, not empty.")

        full_prompt = prompt + st
        out_raw = f"img/raw/s{i:02d}.jpg"
        out_final = f"img/s{i:02d}.jpg"

        if is_gflow:
            if ref == "-":
                cmd = [exe, "image", "t2i", full_prompt, "--model", model_name, "--aspect", ASPECT, "-o", out_raw]
            else:
                p = "img/sheet.jpg" if ref == "sheet" else f"img/ref_{ref}.jpg"
                if not os.path.exists(p) and "--dry" not in a:
                    sys.exit(f"shot {i}: reference {p} is missing (run: pictures.py refs ...)")
                cmd = [exe, "image", "i2i", full_prompt + FIX, "--ref", p, "--model", model_name, "--aspect", ASPECT, "-o", out_raw]
            if pid:
                cmd += ["--project", pid]
        else:
            batch = os.environ.get("STICK_BATCH", "4")
            cmd = [exe, "generate", "create", "flow-text-to-image" if ref == "-" else "flow-image-to-image",
                   "--model", model_name, "--aspect-ratio", ASPECT, "--outputs", "1", "--batch", batch,
                   "--index", str(i), "--folder", folder()]
            if ref != "-":
                p = "img/sheet.jpg" if ref == "sheet" else f"img/ref_{ref}.jpg"
                if not os.path.exists(p) and "--dry" not in a:
                    sys.exit(f"shot {i}: reference {p} is missing (run: pictures.py refs ...)")
                cmd += ["--image", p]
                prompt += FIX
            cmd += ["--prompt", full_prompt]

        if "--dry" in a:
            print(" ".join(cmd[:6]), "... ->", out_raw)
            n += 1
            continue

        print(f"Generating shot {i:02d}...")
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if is_gflow:
            if res.returncode == 0 and os.path.exists(out_raw):
                shutil.copy(out_raw, out_final)
                print(f"shot {i:02d}: OK -> {out_final}")
            else:
                err = (res.stderr or res.stdout).strip().splitlines()
                print(f"shot {i:02d}: error: {err[-1] if err else 'failed'}")
        else:
            print(f"shot {i:02d}: {(res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else '?'}")
        n += 1
    print(f"{n} shots processed.")


def files():
    d = downloads()
    out = {}
    if d and os.path.exists(d):
        for p in sorted(glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.png"))):
            m = re.match(r"(\d+) - ", os.path.basename(p))
            if m:
                out.setdefault(int(m.group(1)), []).append(p)
    return out


def grid(paths, tags, out, tw=400, th=225, cols=5):
    g = Image.new("RGB", (tw * cols, th * ((len(paths) + cols - 1) // cols)), "white")
    d = ImageDraw.Draw(g)
    try:
        f = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        f = ImageFont.load_default()
    for k, (p, t) in enumerate(zip(paths, tags)):
        x, y = k % cols * tw, k // cols * th
        g.paste(Image.open(p).convert("RGB").resize((tw, th)), (x, y))
        if t:
            d.rectangle((x, y, x + 64, y + 32), fill="black")
            d.text((x + 6, y + 3), t, fill="yellow", font=f)
    g.save(out, quality=85)
    print(f"{os.path.abspath(out)}: {len(paths)} pictures")


def review(a):
    lo, hi = (int(a[0]), int(a[1])) if len(a) > 1 else (1, 9999)
    ps = {}
    for p in sorted(glob.glob("img/s*.jpg") + glob.glob("img/s*.png") + glob.glob("img/raw/s*.jpg") + glob.glob("img/raw/s*.png")):
        m = re.search(r"s(\d+)\.", os.path.basename(p))
        if m:
            sid = int(m.group(1))
            if sid not in ps and lo <= sid <= hi:
                ps[sid] = p
    if not ps:
        try:
            fs = files()
            keys = [k for k in sorted(fs) if lo <= k <= hi]
            ps = {k: fs[k][0] for k in keys}
        except Exception:
            pass
    if ps:
        keys = sorted(ps.keys())
        th = 400 if ASPECT == "9:16" else 225
        tw = 225 if ASPECT == "9:16" else 400
        grid([ps[k] for k in keys], [str(k) for k in keys], "review.jpg", tw, th)
    have = {int(r["id"]) for r in shots()}
    gone = sorted(have - set(ps.keys()))
    if gone:
        print("not generated yet:", " ".join(map(str, gone)))


def pick(a):
    swap = {}
    if os.path.exists("PICK.tsv"):
        for r in csv.DictReader(open("PICK.tsv", encoding="utf-8"), delimiter="\t"):
            swap[int(r["shot"])] = int(r["file"])
    os.makedirs("img", exist_ok=True)
    seen, missing = {}, []
    for r in shots():
        s = int(r["id"])
        f = swap.get(s, s)
        raw_candidates = [
            f"img/raw/s{f:02d}.jpg",
            f"img/raw/s{f:02d}.png",
            f"img/s{f:02d}.jpg",
            f"img/s{f:02d}.png"
        ]
        src = next((p for p in raw_candidates if os.path.exists(p)), None)
        if not src:
            try:
                fs = files()
                if f in fs:
                    src = fs[f][0]
            except Exception:
                pass
        if not src:
            missing.append(s)
            continue
        h = hashlib.md5(open(src, "rb").read()).hexdigest()
        if h in seen:
            print(f"WARNING shot {s} is byte-identical to shot {seen[h]}: regenerate it")
        seen[h] = s
        dst = f"img/s{s:02d}.jpg"
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy(src, dst)
    print(f"verified {len(seen)} pictures in img/" + (f"; missing: {' '.join(map(str, missing))}" if missing else ""))


def qc(a):
    ps = sorted(glob.glob("frames/[0-9]*.png"))
    th = 384 if ASPECT == "9:16" else 216
    tw = 216 if ASPECT == "9:16" else 384
    grid(ps, [""] * len(ps), "qc.jpg", tw, th, 6)


EL_PROMPT = (" Hand-drawn 2D cartoon sticker for a stickman explainer: clean dark ink outline, flat bright colours "
             "with soft shading, ONE single isolated subject, centred with a generous empty margin on all sides, on a "
             "solid flat pure magenta background, uniform chroma-key magenta #FF00FF filling the entire background and "
             "showing through every gap, the subject itself contains no magenta or pink anywhere, hard crisp edges, no "
             "drop shadow, no glow, no ground, no scenery, no text, no letters, no numbers, no watermark.")


def elements(a):
    exe = gflow_exe()
    if not exe:
        exe = shutil.which("flow-auto") or sys.exit("gflow-cli (or flow-auto) is not installed.")
    is_gflow = "gflow" in os.path.basename(exe).lower()

    want = ids([x for x in a if not x.startswith("--")])
    rows = list(csv.DictReader(open("ELEMENTS.tsv", encoding="utf-8"), delimiter="\t"))
    os.makedirs("elements/raw", exist_ok=True)
    pid = get_or_create_project(exe) if is_gflow else None
    model_name = MODEL if is_gflow else ("nano-banana-2" if MODEL == "nano2" else MODEL)

    n = 0
    for r in rows:
        i = int(r["id"])
        if want and i not in want:
            continue
        ref = r["ref"].strip()
        prompt = r["object"].strip() + EL_PROMPT
        out_raw = f"elements/raw/{i:02d}.png"

        if is_gflow:
            if ref == "-":
                cmd = [exe, "image", "t2i", prompt, "--model", model_name, "--aspect", "1:1", "-o", out_raw]
            else:
                p = "img/sheet.jpg" if ref == "sheet" else f"img/ref_{ref}.jpg"
                cmd = [exe, "image", "i2i", prompt + FIX, "--ref", p, "--model", model_name, "--aspect", "1:1", "-o", out_raw]
            if pid:
                cmd += ["--project", pid]
        else:
            cmd = [exe, "generate", "create", "flow-text-to-image" if ref == "-" else "flow-image-to-image",
                   "--model", model_name, "--aspect-ratio", "1:1", "--outputs", "1",
                   "--batch", os.environ.get("STICK_BATCH", "4"), "--index", str(i),
                   "--folder", folder() + "_el", "--prompt", prompt]
            if ref != "-":
                cmd += ["--image", "img/sheet.jpg" if ref == "sheet" else f"img/ref_{ref}.jpg"]

        if "--dry" in a:
            print(" ".join(cmd[:6]), "... ->", out_raw)
            n += 1
            continue

        print(f"Generating element {i:02d}...")
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if is_gflow:
            if res.returncode == 0 and os.path.exists(out_raw):
                print(f"element {i:02d}: OK -> {out_raw}")
            else:
                err = (res.stderr or res.stdout).strip().splitlines()
                print(f"element {i:02d}: error: {err[-1] if err else 'failed'}")
        else:
            print(f"element {i:02d}: {(res.stdout or res.stderr).strip()[:60]}")
        n += 1
    print(f"{n} elements processed.")


def cutouts(a):
    os.makedirs("elements/cut", exist_ok=True)
    os.makedirs("elements/raw", exist_ok=True)
    rows = list(csv.DictReader(open("ELEMENTS.tsv", encoding="utf-8"), delimiter="\t"))
    for r in rows:
        i = int(r["id"])
        raw_p = f"elements/raw/{i:02d}.png"
        raw_jpg = f"elements/raw/{i:02d}.jpg"
        if not os.path.exists(raw_p) and os.path.exists(raw_jpg):
            im = Image.open(raw_jpg).convert("RGB")
            im.save(raw_p)
    keyer = os.path.join(os.path.dirname(os.path.abspath(__file__)), "key.py")
    res = subprocess.run([sys.executable, keyer, "elements/raw", "elements/cut"], capture_output=True, text=True)
    print((res.stdout or res.stderr).strip()[-600:])
    ps = sorted(glob.glob("elements/cut/*.png"))
    if ps:
        g = []
        for p in ps:
            im = Image.open(p).convert("RGBA")
            t = Image.new("RGBA", im.size, (150, 150, 150, 255))
            t.alpha_composite(im)
            g.append(t)
        tmp = []
        for k, t in enumerate(g):
            q = f"elements/_prev{k}.png"
            t.convert("RGB").save(q)
            tmp.append(q)
        grid(tmp, [os.path.basename(p)[:2] for p in ps], "cutouts.jpg", 300, 300, 6)
        for q in tmp:
            if os.path.exists(q):
                os.remove(q)


if __name__ == "__main__":
    cmds = {"refs": refs, "submit": submit, "review": review, "pick": pick, "qc": qc,
            "elements": elements, "cutouts": cutouts}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](sys.argv[2:])
