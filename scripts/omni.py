#!/usr/bin/env python3
"""Google Omni через провайдера 创艺坊 (Yiciyuang): продление видео, перерисовка видео, ролик из фото.

Только стандартная библиотека Python 3.9+. Для prep нужен ffmpeg.

  python3 omni.py check                                   проверить ключ (бесплатно)
  python3 omni.py prep in.mov clip.mp4 [--start 0] [--dur 10] [--tail 11.9] [--vertical]
                                                          кусок 1280x720 (или 720x1280), 24 к/с, до 12 с
  python3 omni.py run NAME --model omni-extend-20s --prompt-file p.txt --video clip.mp4 [--image face.jpg]
  python3 omni.py run NAME --model omni-fast-v2v   --prompt-file p.txt --video clip.mp4 [--image ref.jpg]
  python3 omni.py run NAME --model omni-fast-no-water --prompt-file p.txt [--image face.jpg]
  python3 omni.py run NAME --model omni-fast-no-water --prompt-file p.txt --image first.jpg --last-image last.jpg
                                                          клип начнётся с первого кадра и закончится на втором
  python3 omni.py newpart out/ext1.mp4 clip.mp4 ext1_new.mp4   вырезать из продления только новую часть
  python3 omni.py join a.mp4 b.mp4 c.mp4 -o final.mp4 [--delogo]   склеить куски без рассинхрона
  добавьте --dry-run, чтобы увидеть запрос и цену без отправки.

Ключ: переменная YICIYUANG_API_KEY или строка YICIYUANG_API_KEY=... в файле .env
(текущая папка, корень набора или ~/.yiciyuang.env). Ключ не печатается.

Локальные файлы провайдер не принимает: нужна публичная ссылка. Скрипт сам выкладывает файл
во временное хранилище (--host uguu: uguu.se, удаляется через 3 часа; запасной --host litterbox: 1 час)
или берёт готовую ссылку https://... вместо пути. Не выкладывайте чужие лица и приватное видео.

Заявка отправляется один раз: состояние пишется в OUT/NAME.ledger.json. Повторный запуск с тем же
именем не платит второй раз, а дозабирает результат.
"""
import argparse, base64, json, mimetypes, os, shutil, subprocess, sys, time, urllib.error, urllib.request, uuid

API = "https://api.yiciyuang.com"
PRICES = {"omni-fast-no-water": 0.75, "omni-fast": 0.69, "omni-extend-20s": 0.75, "omni-fast-v2v": 0.82}
NEEDS_VIDEO = ("omni-extend-20s", "omni-fast-v2v")
BASE_MODELS = ("omni-fast-no-water", "omni-fast")
UA = "omni-video-kit/1.0"
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_key():
    if os.environ.get("YICIYUANG_API_KEY"):
        return os.environ["YICIYUANG_API_KEY"].strip()
    for p in (".env", os.path.join(HERE, "..", ".env"), os.path.expanduser("~/.yiciyuang.env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if line.strip().startswith("YICIYUANG_API_KEY="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
    sys.exit("Нет ключа. Создайте файл .env со строкой YICIYUANG_API_KEY=sk-... (ключ из раздела «令牌 / Токены» на newapi.yiciyuang.com)")


def http(method, url, key, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:500]}


def multipart(url, fields, file_field, path, timeout=600):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{os.path.basename(path)}"\r\n'
                 f'Content-Type: {ctype}\r\n\r\n'.encode())
    body = b"".join(parts) + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={b}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors="replace").strip()
    except urllib.error.HTTPError as e:
        sys.exit(f"хранилище {url.split('/')[2]} отказало: HTTP {e.code} {e.read().decode(errors='replace')[:150]}. "
                 "Попробуйте другой --host или дайте свою публичную ссылку https://...mp4")


def publish(path, host):
    """Локальный файл -> временная публичная ссылка. Готовую ссылку возвращает как есть."""
    if path.startswith(("http://", "https://")):
        return path
    if os.path.getsize(path) > 100 * 1024 * 1024:
        sys.exit(f"{path}: больше 100 МБ, сначала сделайте prep")
    if host == "uguu":
        j = json.loads(multipart("https://uguu.se/upload", {}, "files[]", path))
        if not j.get("success"):
            sys.exit(f"uguu.se не принял файл: {json.dumps(j, ensure_ascii=False)[:200]}")
        url = j["files"][0]["url"]
    elif host == "litterbox":
        url = multipart("https://litterbox.catbox.moe/resources/internals/api.php",
                        {"reqtype": "fileupload", "time": "1h"}, "fileToUpload", path)
    else:
        sys.exit(f"неизвестный --host {host}")
    if not url.startswith("https://"):
        sys.exit(f"хранилище ответило не ссылкой: {url[:200]}")
    return url


def check_url(url):
    req = urllib.request.Request(url, headers={"Range": "bytes=0-1023", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        sys.exit(f"ссылка {url} не открывается: HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        log(f"предупреждение: не смог сам открыть {url} ({e}); отправляю как есть")
        return "?"
    if "text/html" in ctype:
        sys.exit(f"ссылка {url} отдаёт страницу, а не файл ({ctype}); нужна прямая ссылка на mp4/jpg")
    return ctype


def cmd_check(_):
    key = load_key()
    code, j = http("GET", f"{API}/v1/models", key, timeout=30)
    if code != 200:
        sys.exit(f"Ключ не принят: HTTP {code} {json.dumps(j, ensure_ascii=False)[:300]}")
    ids = {m.get("id") for m in j.get("data", [])}
    for m in PRICES:
        print(f"{m:22} {'есть' if m in ids else 'НЕТ на ключе'}  ~¥{PRICES[m]} за запуск")
    print("Ключ работает.")


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def need_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.exit("нужны ffmpeg и ffprobe")


def cmd_prep(a):
    need_ffmpeg()
    start, dur = a.start, a.dur
    if a.tail:
        total = duration(a.src)
        dur = min(a.tail, total)
        start = max(0.0, total - dur)
    dur = min(dur, 12.0)
    w, h = (720, 1280) if a.vertical else (1280, 720)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", a.src, "-t", f"{dur:.3f}", "-vf", vf,
           "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           "-movflags", "+faststart", a.out]
    subprocess.run(cmd, check=True)
    print(f"{a.out}: {os.path.getsize(a.out) // 1024} КБ, {duration(a.out):.2f} с (с {start:.2f} с исходника)")


def cmd_newpart(a):
    need_ffmpeg()
    cut = duration(a.input) + a.shift
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{cut:.3f}", "-i", a.result, "-c:v", "libx264", "-crf", "18",
                    "-preset", "veryfast", "-r", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                    "-movflags", "+faststart", a.out], check=True)
    print(f"{a.out}: новая часть {duration(a.out):.2f} с (с {cut:.2f} с результата)")


def cmd_join(a):
    need_ffmpeg()
    n = len(a.parts)
    w, h = (720, 1280) if a.vertical else (1280, 720)
    chains, ins = [], []
    for i, p in enumerate(a.parts):
        ins += ["-i", p]
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                      f"fps=24,format=yuv420p,setsar=1[v{i}];[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    joined = "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][a]"
    tail = ";[vc]delogo=x=1130:y=570:w=62:h=62[v]" if a.delogo and not a.vertical else ";[vc]null[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(chains) + ";" + joined + tail,
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", a.out], check=True)
    print(f"{a.out}: {duration(a.out):.2f} с из {n} кусков")


def cmd_run(a):
    if a.model in NEEDS_VIDEO and not a.video:
        sys.exit(f"модели {a.model} нужен --video")
    if a.last_image and (not a.image or a.model not in BASE_MODELS):
        sys.exit("--last-image работает только вместе с --image и моделями " + ", ".join(BASE_MODELS))
    for p in (a.image, a.last_image, a.video):
        if p and not p.startswith("http") and not os.path.exists(p):
            sys.exit(f"нет файла {p}")
    prompt = open(a.prompt_file, encoding="utf-8").read().strip()
    os.makedirs(a.out_dir, exist_ok=True)
    ledger_path = os.path.join(a.out_dir, a.name + ".ledger.json")
    out = os.path.join(a.out_dir, a.name + ".mp4")
    ledger = json.load(open(ledger_path)) if os.path.exists(ledger_path) else {}
    price = PRICES.get(a.model, "?")

    if a.dry_run:
        print(json.dumps({"model": a.model, "prompt": prompt[:120] + ("…" if len(prompt) > 120 else ""),
                          "video": a.video, "image": a.image, "last_image": a.last_image, "price_cny": price}, ensure_ascii=False, indent=1))
        return
    key = load_key()
    task = ledger.get("task")
    if not task:
        if ledger.get("state") == "SUBMITTING":
            sys.exit("Прошлая отправка не подтвердилась. Проверьте журнал на сайте; повтор только после удаления ledger-файла.")
        body = {"model": a.model, "prompt": prompt}
        if a.model in BASE_MODELS:
            body.update({"duration": a.duration, "size": a.size})
        if a.video:
            body["video_url"] = publish(a.video, a.host)
            log(f"видео: {body['video_url']} ({check_url(body['video_url'])})")
        if a.image:
            img = publish(a.image, a.host)
            check_url(img)
            body["images"] = [img]
            if a.last_image:  # два кадра: первый и последний
                last = publish(a.last_image, a.host)
                check_url(last)
                body["images"].append(last)
            if a.video:
                body["image_url"] = img
        json.dump({"state": "SUBMITTING", "body": body, "at": time.time()}, open(ledger_path, "w"), ensure_ascii=False, indent=1)
        log(f"отправляю {a.name}: {a.model}, ~¥{price}")
        code, j = http("POST", f"{API}/v1/videos", key, body)
        task = j.get("id") or j.get("task_id")
        if code >= 300 or not task:
            json.dump({"state": "REJECTED", "http": code, "answer": j}, open(ledger_path, "w"), ensure_ascii=False, indent=1)
            sys.exit(f"Отказ HTTP {code}: {json.dumps(j, ensure_ascii=False)[:400]}")
        ledger = {"state": "SUBMITTED", "task": task, "body": body, "submitted_at": time.time()}
        json.dump(ledger, open(ledger_path, "w"), ensure_ascii=False, indent=1)
        log(f"задача {task}")
    t0, last = time.time(), None
    while True:
        code, j = http("GET", f"{API}/v1/videos/{task}", key, timeout=30)
        st = j.get("status")
        if st != last:
            log(f"статус {st} {j.get('progress') or ''}")
            last = st
        if st == "completed":
            break
        if st in ("failed", "cancelled", "error"):
            ledger.update({"state": "FAILED", "answer": j})
            json.dump(ledger, open(ledger_path, "w"), ensure_ascii=False, indent=1)
            sys.exit(f"Задача упала: {json.dumps(j.get('error') or j, ensure_ascii=False)[:400]}")
        if time.time() - t0 > a.limit:
            sys.exit("Долго ждём. Задача не отменена: запустите ту же команду ещё раз, чтобы дозабрать.")
        time.sleep(10)
    req = urllib.request.Request(f"{API}/v1/videos/{task}/content", headers={"Authorization": "Bearer " + key, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(out, "wb") as f:
        shutil.copyfileobj(r, f)
    ledger.update({"state": "DONE", "out": out, "seconds": int(time.time() - ledger.get("submitted_at", t0))})
    json.dump(ledger, open(ledger_path, "w"), ensure_ascii=False, indent=1)
    log(f"готово: {out} ({os.path.getsize(out) // 1024} КБ)")


def main():
    ap = argparse.ArgumentParser(description="Google Omni через Yiciyuang")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check").set_defaults(fn=cmd_check)
    p = sp.add_parser("prep")
    p.add_argument("src"); p.add_argument("out")
    p.add_argument("--start", type=float, default=0); p.add_argument("--dur", type=float, default=10)
    p.add_argument("--tail", type=float, help="взять последние N секунд (для следующего продления: 11.9)")
    p.add_argument("--vertical", action="store_true", help="720x1280 вместо 1280x720")
    p.set_defaults(fn=cmd_prep)
    q = sp.add_parser("newpart")
    q.add_argument("result"); q.add_argument("input"); q.add_argument("out")
    q.add_argument("--shift", type=float, default=0.0, help="сдвиг точки реза, с (0.5 убирает «залипание» на старте)")
    q.set_defaults(fn=cmd_newpart)
    j = sp.add_parser("join")
    j.add_argument("parts", nargs="+"); j.add_argument("-o", "--out", required=True)
    j.add_argument("--delogo", action="store_true", help="затереть значок Gemini справа внизу (только 1280x720)")
    j.add_argument("--vertical", action="store_true")
    j.set_defaults(fn=cmd_join)
    r = sp.add_parser("run")
    r.add_argument("name"); r.add_argument("--model", required=True, choices=list(PRICES))
    r.add_argument("--prompt-file", required=True)
    r.add_argument("--video"); r.add_argument("--image")
    r.add_argument("--last-image", help="второй кадр: клип закончится на нём (только с --image, модели omni-fast*)")
    r.add_argument("--duration", type=int, default=10); r.add_argument("--size", default="1280x720")
    r.add_argument("--host", default="uguu", choices=["uguu", "litterbox"])
    r.add_argument("--out-dir", default="out"); r.add_argument("--limit", type=int, default=1500)
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
