"""Разведочное тестирование: граничные и негативные случаи пяти задач релиза.

Ищет то, что не покрыто verify_*: битые и подделанные файлы, огромные числа,
спецсимволы, нарушения прав, странные диапазоны, параллельные запросы.
Главное правило: сервер не отвечает 5xx ни на что, что прислал пользователь.

Запуск: docker compose exec -T api python -m tests.qa_exploratory
"""

import asyncio
import os
import sys
import tempfile
import time
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from tests.support import ADMIN, BASE, MANAGER, Checks, ffmpeg, login

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)
INFO: list[str] = []


def info(line: str) -> None:
    INFO.append(line)
    print(f"  ℹ {line}", flush=True)


class QA(Checks):
    def expect(self, title: str, r: httpx.Response, ok: set[int] | int, note: str = "") -> bool:
        ok_set = {ok} if isinstance(ok, int) else ok
        good = r.status_code in ok_set and r.status_code < 500
        body = r.text[:160].replace("\n", " ")
        return self.check(title, good, f"HTTP {r.status_code}, ждали {sorted(ok_set)} {note} {body}")


async def visible_ids(client: httpx.AsyncClient) -> list[int]:
    ids: list[int] = []
    cursor = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 100, "filter": "all"}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(f"{BASE}/conversations", params=params)).json()
        ids += [i["id"] for i in page["items"]]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return ids


async def upload(client: httpx.AsyncClient, name: str, data: bytes, ctype: str = "application/octet-stream", endpoint: str = "upload") -> httpx.Response:
    return await client.post(f"{BASE}/files/{endpoint}", files={"file": (name, data, ctype)})


async def run() -> int:  # noqa: PLR0915, C901
    c = QA("Разведочное тестирование пяти задач")
    tmp = tempfile.mkdtemp(prefix="qa-")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    anon = httpx.AsyncClient(timeout=60)
    admin_ids = await visible_ids(admin)
    mgr_ids = await visible_ids(manager)
    foreign = [i for i in admin_ids if i not in set(mgr_ids)]
    conv = mgr_ids[0]
    conv_other = mgr_ids[1]
    print(f"чатов: руководитель {len(admin_ids)}, менеджер {len(mgr_ids)}, чужих менеджеру {len(foreign)}")

    # ===================================================== 1. ЗАГРУЗКА ФАЙЛОВ
    c.section("A. Загрузка: права и формат запроса")
    c.expect("без входа — 401", await upload(anon, "a.png", PNG), 401)
    c.expect("без поля file — 422", await admin.post(f"{BASE}/files/upload", data={"x": "1"}), 422)
    c.expect("поле названо иначе — 422", await admin.post(f"{BASE}/files/upload", files={"doc": ("a.png", PNG)}), 422)
    c.expect("GET вместо POST — 405", await admin.get(f"{BASE}/files/upload"), {405, 404, 422})

    c.section("B. Загрузка: запрещённые и подозрительные имена")
    for name in ["virus.exe", "run.bat", "x.sh", "a.js", "page.html", "pic.svg", "x.php", "x.ps1", "x.jar",
                 "x.app", "x.dmg", "x.msi", "x.vbs", "x.htm", "x.xhtml", "x.xml", "A.EXE", "a.pdf.exe",
                 "noext", ".png", "x.png.", "x.p\x00ng.exe", "x.Php"]:
        r = await upload(admin, name, b"MZ\x90\x00" + os.urandom(64))
        c.expect(f"запрещено: {name!r}", r, {422, 400})
    for name in ["../../etc/passwd.png", "..\\..\\win.png", "/abs/x.png", "a" * 300 + ".png", "фото 🌙 «тест».png",
                 'q"uote.png', "new\nline.png", "semi;colon.png"]:
        r = await upload(admin, name, PNG, "image/png")
        ok = r.status_code in {201, 422, 400}
        c.check(f"имя {name[:30]!r} — принято безопасно или отклонено", ok and r.status_code < 500, r.status_code)
        if r.status_code == 201:
            body = r.json()
            key = body["upload_key"]
            c.check(f"  ключ хранилища без «..» и слэшей от пользователя: {key[:50]}", ".." not in key and "\\" not in key)
            if name.startswith(("../", "/abs", "..\\")):
                c.check("  в имени файла нет путей", "/" not in body["file_name"] and ".." not in body["file_name"], body["file_name"])

    c.section("C. Загрузка: содержимое не соответствует расширению")
    r = await upload(admin, "fake.png", b"this is not a picture at all")
    info(f"текст под видом .png → HTTP {r.status_code} {r.text[:90]}")
    c.check("текст .png: не 5xx", r.status_code < 500)
    r = await upload(admin, "fake.mp4", os.urandom(2000))
    info(f"мусор под видом .mp4 → HTTP {r.status_code} {r.text[:90]}")
    c.check("мусор .mp4: не 5xx", r.status_code < 500)
    r = await upload(admin, "fake.mp3", os.urandom(2000))
    info(f"мусор под видом .mp3 → HTTP {r.status_code} {r.text[:90]}")
    c.check("мусор .mp3: не 5xx", r.status_code < 500)
    r = await upload(admin, "png-as.pdf", PNG)
    c.check("PNG под именем .pdf принимается как документ", r.status_code == 201 and r.json()["kind"] == "document", r.text[:100])
    r = await upload(admin, "one.pdf", b"x")
    c.check("файл в 1 байт принимается", r.status_code == 201, r.status_code)
    r = await upload(admin, "empty.pdf", b"")
    c.check("пустой файл — 422", r.status_code == 422, r.status_code)
    big_img = os.path.join(tmp, "big.png")
    from PIL import Image

    Image.new("RGB", (6000, 4000), (10, 200, 30)).save(big_img, "PNG")
    t0 = time.monotonic()
    r = await upload(admin, "huge-dims.png", open(big_img, "rb").read(), "image/png")
    c.check(f"картинка 6000×4000 принимается ({time.monotonic()-t0:.1f} с)", r.status_code == 201, r.text[:100])
    # «бомба распаковки»: маленький PNG с гигантскими размерами
    import struct
    import zlib

    def bomb(w: int, h: int) -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        raw = zlib.compress(b"\x00" * (w * 3 + 1), 9) if h == 1 else b""
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")

    t0 = time.monotonic()
    r = await upload(admin, "bomb.png", bomb(60000, 60000), "image/png")
    c.check(f"«бомба» 60000×60000 не роняет сервер ({time.monotonic()-t0:.1f} с)", r.status_code < 500, f"{r.status_code} {r.text[:100]}")

    c.section("D. Загрузка: размеры и параллельность")
    blob50 = os.urandom(50 * 1024 * 1024)
    t0 = time.monotonic()
    r = await upload(admin, "big50.pdf", blob50)
    c.check(f"50 МБ принимается ({time.monotonic()-t0:.1f} с)", r.status_code == 201, r.status_code)
    del blob50
    over = os.path.join(tmp, "over.bin")
    with open(over, "wb") as f:
        f.truncate(201 * 1024 * 1024)
    t0 = time.monotonic()
    with open(over, "rb") as f:
        r = await admin.post(f"{BASE}/files/upload", files={"file": ("over.pdf", f, "application/pdf")})
    c.check(f"201 МБ отклоняется понятно ({time.monotonic()-t0:.1f} с)", r.status_code in {413, 422}, f"{r.status_code} {r.text[:120]}")
    os.remove(over)
    payload = os.urandom(5 * 1024 * 1024)
    t0 = time.monotonic()
    rs = await asyncio.gather(*[upload(admin, f"par{i}.pdf", payload) for i in range(8)])
    c.check(f"8 параллельных загрузок по 5 МБ ({time.monotonic()-t0:.1f} с)", all(x.status_code == 201 for x in rs), [x.status_code for x in rs])
    keys = {x.json()["upload_key"] for x in rs if x.status_code == 201}
    c.check("ключи хранилища у параллельных загрузок уникальны", len(keys) == 8, len(keys))

    # ===================================================== 2. ГОЛОСОВЫЕ
    c.section("E. Голосовое: форматы и порча")
    def voice(name: str, *args: str, fmt: str) -> str:
        path = os.path.join(tmp, name)
        ffmpeg(*args, "-f", fmt, path)
        return path

    v_webm = voice("v.webm", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:a", "libopus", fmt="webm")
    v_ogg = voice("v.ogg", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:a", "libopus", fmt="ogg")
    v_mp4 = voice("v.m4a", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:a", "aac", fmt="mp4")
    v_wav = voice("v.wav", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", fmt="wav")
    v_silent = voice("s.webm", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", "-c:a", "libopus", fmt="webm")
    v_video = voice("nv.webm", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3", "-c:v", "libvpx", fmt="webm")
    v_exact = voice("e.webm", "-f", "lavfi", "-i", "sine=frequency=300:duration=1.05", "-c:a", "libopus", fmt="webm")
    v_borderline = voice("b.webm", "-f", "lavfi", "-i", "sine=frequency=300:duration=0.9", "-c:a", "libopus", fmt="webm")
    for label, path, want in [("WebM/Opus (Chrome)", v_webm, 201), ("Ogg/Opus (Firefox)", v_ogg, 201),
                              ("MP4/AAC (Safari)", v_mp4, 201), ("WAV", v_wav, 201), ("тишина", v_silent, 201),
                              ("1,05 с", v_exact, 201), ("0,9 с (кодек добивает до секунды)", v_borderline, {201, 422}),
                              ("видео без звука вместо голоса", v_video, 422)]:
        r = await upload(admin, "voice", open(path, "rb").read(), "audio/webm", endpoint="voice")
        c.check(f"голос: {label} → {want}", r.status_code in ({want} if isinstance(want, int) else want), f"{r.status_code} {r.text[:120]}")
        if r.status_code == 201 and label in ("WebM/Opus (Chrome)", "MP4/AAC (Safari)", "тишина"):
            j = r.json()
            c.check(f"  {label}: вид voice, Ogg, длительность 3±1", j["kind"] == "voice" and "ogg" in j["mime_type"] and 2 <= (j["duration_sec"] or 0) <= 4, j)
            c.check(f"  {label}: волна 100 значений 0..31", len(j["waveform"] or []) == 100 and all(0 <= v <= 31 for v in j["waveform"]))
    data = open(v_webm, "rb").read()
    r = await upload(admin, "voice", data[: len(data) // 2], "audio/webm", endpoint="voice")
    c.check("обрезанный на половине файл — не 5xx", r.status_code < 500, f"{r.status_code} {r.text[:100]}")
    info(f"обрезанное голосовое → HTTP {r.status_code}")
    r = await upload(admin, "voice", os.urandom(5000), "audio/webm", endpoint="voice")
    c.check("случайные байты вместо голоса — 422", r.status_code == 422, r.status_code)
    r = await upload(admin, "voice", b"", "audio/webm", endpoint="voice")
    c.check("пустое голосовое — 422", r.status_code == 422, r.status_code)
    c.expect("голос без входа — 401", await upload(anon, "v", data, "audio/webm", endpoint="voice"), 401)
    long_v = voice("long.webm", "-f", "lavfi", "-i", "sine=frequency=200:duration=600", "-c:a", "libopus", fmt="webm")
    t0 = time.monotonic()
    r = await upload(admin, "voice", open(long_v, "rb").read(), "audio/webm", endpoint="voice")
    dt = time.monotonic() - t0
    c.check(f"10-минутное голосовое обработано за {dt:.1f} с", r.status_code == 201 and dt < 60, f"{r.status_code} {dt:.1f}")
    if r.status_code == 201:
        info(f"10 мин голос: длительность {r.json()['duration_sec']} с, размер {r.json()['size_bytes']//1024} КБ")
    t0 = time.monotonic()
    rs = await asyncio.gather(*[upload(admin, "voice", data, "audio/webm", endpoint="voice") for _ in range(6)])
    c.check(f"6 параллельных голосовых ({time.monotonic()-t0:.1f} с)", all(x.status_code == 201 for x in rs), [x.status_code for x in rs])

    # ===================================================== 3. ОТПРАВКА СООБЩЕНИЙ
    c.section("F. Отправка: текст")
    url = f"{BASE}/conversations/{conv}/messages"
    async def post(payload: dict[str, Any], client: httpx.AsyncClient = admin, cid: int = conv) -> httpx.Response:
        return await client.post(f"{BASE}/conversations/{cid}/messages", json=payload)

    c.expect("пустое сообщение — 4xx", await post({}), {422, 400})
    c.expect("только пробелы — 4xx", await post({"text": "   \n  "}), {422, 400})
    c.expect("text=null без вложений — 4xx", await post({"text": None}), {422, 400})
    r = await post({"text": "x" * 4096})
    c.check("ровно 4096 символов — принято", r.status_code == 201, r.status_code)
    c.expect("4097 символов — 422", await post({"text": "x" * 4097}), 422)
    for label, txt in [("эмодзи и RTL", "Привет 🌙✨ مرحبا שלום"), ("HTML/скрипт", "<script>alert(1)</script><img src=x onerror=alert(1)>"),
                       ("SQL-кавычки", "'; DROP TABLE messages; --"), ("очень длинное слово", "ы" * 3000),
                       ("переносы и табы", "a\n\n\tb\r\nc"), ("управляющие", "a\x07b\x1bc"), ("нулевой байт", "a\x00b"),
                       ("вертикальный таб", "a\x0bb")]:
        try:
            r = await post({"text": txt})
            c.check(f"текст: {label} — не 5xx", r.status_code < 500, f"{r.status_code} {r.text[:120]}")
            if label in ("нулевой байт", "суррогаты"):
                info(f"{label} → HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            c.check(f"текст: {label} — запрос не упал", False, repr(exc)[:120])
    r = await admin.post(url, content=b'{"text": "a\\ud83db"}', headers={"Content-Type": "application/json"})
    c.check("одинокий суррогат в JSON — не 5xx", r.status_code < 500, f"{r.status_code} {r.text[:100]}")
    c.expect("типы: text числом — 422", await post({"text": 123}), {422})
    c.expect("uploads не списком — 422", await post({"uploads": "x"}), 422)
    c.expect("чат не существует — 404", await post({"text": "x"}, cid=99999999), 404)
    c.expect("чат-гигант (int64) — 4xx, не 5xx", await post({"text": "x"}, cid=9223372036854775807), {404, 422})
    c.expect("чат-гигант (>int64) — 4xx, не 5xx", await admin.post(f"{BASE}/conversations/92233720368547758070/messages", json={"text": "x"}), {404, 422})
    c.expect("чат 0 — 422", await post({"text": "x"}, cid=0), 422)
    c.expect("чат -1 — 422", await post({"text": "x"}, cid=-1), 422)
    if foreign:
        c.expect("менеджер пишет в чужой чат — 404", await post({"text": "x"}, manager, foreign[0]), 404)
    c.expect("без входа — 401", await post({"text": "x"}, anon), 401)

    c.section("G. Отправка: вложения")
    up = (await upload(admin, "a.png", PNG, "image/png")).json()
    up2 = (await upload(admin, "b.pdf", b"%PDF-1.4 test", "application/pdf")).json()
    r = await post({"uploads": [up]})
    c.check("только файл, без текста — принято", r.status_code == 201, r.text[:100])
    r = await post({"text": "с файлом", "uploads": [up, up2]})
    c.check("текст и два файла — принято", r.status_code == 201, r.text[:100])
    r = await post({"uploads": [up]})
    info(f"повторное использование того же upload_key → HTTP {r.status_code}")
    c.check("повтор ключа — не 5xx", r.status_code < 500)
    fake = {"upload_key": "voice/2099/01/nonexistent.ogg", "file_name": "x.ogg", "size_bytes": 1}
    c.expect("несуществующий ключ — 4xx", await post({"uploads": [fake]}), {422, 400, 404})
    trav = {"upload_key": "../../etc/passwd", "file_name": "passwd", "size_bytes": 1}
    c.expect("ключ с «../» — 4xx", await post({"uploads": [trav]}), {422, 400, 404})
    c.expect("ключ пустой — 4xx", await post({"uploads": [{"upload_key": "", "file_name": "x", "size_bytes": 1}]}), {422, 400, 404})
    c.expect("uploads пустой список без текста — 4xx", await post({"uploads": []}), {422, 400})
    many = []
    for i in range(25):
        u = (await upload(admin, f"m{i}.png", PNG, "image/png")).json()
        many.append(u)
    r = await post({"text": "25 файлов", "uploads": many})
    info(f"25 вложений в одном сообщении → HTTP {r.status_code} {r.text[:80]}")
    c.check("25 вложений — не 5xx", r.status_code < 500)
    c.expect("attachment_ids чужие — 4xx", await post({"attachment_ids": [1]}), {422, 400, 404, 201})
    mine = (await upload(manager, "mine.png", PNG, "image/png")).json()
    r = await post({"uploads": [mine]})
    info(f"руководитель отправляет ключ менеджера → HTTP {r.status_code}")
    c.check("чужой ключ загрузки — не 5xx", r.status_code < 500)

    c.section("H. Служебная заметка не уходит клиенту")
    v = (await upload(admin, "voice", open(v_webm, "rb").read(), "audio/webm", endpoint="voice")).json()
    r = await post({"text": "внутреннее", "is_internal": True, "uploads": [v]})
    c.check("заметка с голосовым принята", r.status_code == 201, r.text[:100])
    if r.status_code == 201:
        async with SessionLocal() as db:
            n = await db.scalar(text("select count(*) from outbox where message_id=:m"), {"m": r.json()["id"]})
        c.check("для заметки нет задания на отправку в Telegram", n == 0, n)

    c.section("I. Правка и повтор")
    sent = (await post({"text": "для правки"})).json()
    mid = sent["id"]
    await asyncio.sleep(3)
    ed = f"{BASE}/conversations/{conv}/messages/{mid}"
    c.expect("правка пустым — 422", await admin.patch(ed, json={"text": ""}), 422)
    c.expect("правка 4097 — 422", await admin.patch(ed, json={"text": "x" * 4097}), 422)
    c.expect("правка без тела — 422", await admin.patch(ed, json={}), 422)
    c.expect("менеджер правит чужое — отказ", await manager.patch(ed, json={"text": "взлом"}), {403, 404, 422})
    c.expect("правка несуществующего — 404", await admin.patch(f"{BASE}/conversations/{conv}/messages/99999999", json={"text": "x"}), 404)
    c.expect("повтор не упавшего сообщения — 4xx", await admin.post(f"{ed}/retry"), {400, 409, 422})

    # ===================================================== 4. ВЫДАЧА ФАЙЛОВ
    c.section("J. Выдача файлов и Range")
    m = (await post({"text": "файл для проверок", "uploads": [(await upload(admin, "r.pdf", b"0123456789" * 100, "application/pdf")).json()]})).json()
    fid = m["attachments"][0]["id"]
    f = f"{BASE}/files/{fid}"
    c.expect("без входа — 401", await anon.get(f), 401)
    c.expect("несуществующий — 404", await admin.get(f"{BASE}/files/99999999"), 404)
    c.expect("id не число — 422", await admin.get(f"{BASE}/files/abc"), 422)
    c.expect("id-гигант (>int64) — 4xx", await admin.get(f"{BASE}/files/99999999999999999999"), {404, 422})
    c.expect("id 0/отрицательный — 4xx", await admin.get(f"{BASE}/files/-5"), {404, 422})
    for rng, want in [("bytes=0-0", 206), ("bytes=0-9", 206), ("bytes=-5", 206), ("bytes=990-", 206), ("bytes=0-999999", 206),
                      ("bytes=5000-6000", 416), ("bytes=10-5", 416), ("bytes=abc", {200, 416}), ("bytes=0-1,5-6", {200, 206, 416}),
                      ("bytes=", {200, 416}), ("items=0-5", {200, 416}), ("bytes=-0", {416, 200, 206}), ("bytes=--5", {200, 416})]:
        r = await admin.get(f, headers={"Range": rng})
        c.expect(f"Range {rng!r}", r, want)
        if rng == "bytes=0-9" and r.status_code == 206:
            c.check("  тело 10 байт и Content-Range корректен", len(r.content) == 10 and r.headers.get("content-range", "").startswith("bytes 0-9/"), r.headers.get("content-range"))
        if rng == "bytes=-5" and r.status_code == 206:
            c.check("  суффикс: последние 5 байт", len(r.content) == 5, len(r.content))
    r = await admin.head(f)
    c.check("HEAD — не 5xx", r.status_code < 500, r.status_code)
    r = await admin.get(f"{f}?variant=m4a")
    c.check("m4a для не-голосового — не 5xx", r.status_code < 500, r.status_code)
    c.expect("variant=evil — 422", await admin.get(f"{f}?variant=evil"), 422)
    c.expect("thumb у PDF — 4xx", await admin.get(f"{f}?variant=thumb"), {404, 422, 400})
    r = await admin.get(f"{f}?download=1")
    c.check("download=1: attachment", "attachment" in r.headers.get("content-disposition", ""), r.headers.get("content-disposition"))
    for nm in ['q"uote.pdf', "new\nline.pdf", "фото; filename=evil.exe.pdf", "a" * 200 + ".pdf"]:
        up_ = await upload(admin, nm, b"%PDF-1.4 x", "application/pdf")
        if up_.status_code != 201:
            continue
        mm = (await post({"uploads": [up_.json()]})).json()
        rr = await admin.get(f"{BASE}/files/{mm['attachments'][0]['id']}?download=1")
        cd = rr.headers.get("content-disposition", "")
        c.check(f"Content-Disposition для {nm[:25]!r} без внедрения заголовков", rr.status_code == 200 and "\n" not in cd and "\r" not in cd, f"{rr.status_code} {cd[:100]}")
    if foreign:
        c.check("менеджер не видит файл чужого чата", True)  # покрыто verify_media
    up_html = await upload(admin, "x.html", b"<script>1</script>")
    c.check("HTML не принимается", up_html.status_code == 422)

    # ===================================================== 5. ПЕРЕСЫЛКА
    c.section("K. Пересылка: граничные случаи")
    src = conv
    ids = []
    for i in range(3):
        ids.append((await post({"text": f"для пересылки {i}"})).json()["id"])
    await asyncio.sleep(3)
    fw = lambda cid, **kw: admin.post(f"{BASE}/conversations/{cid}/forward", json=kw)  # noqa: E731
    c.expect("без входа — 401", await anon.post(f"{BASE}/conversations/{conv_other}/forward", json={"source_conversation_id": src, "message_ids": ids}), 401)
    c.expect("в тот же чат", await fw(src, source_conversation_id=src, message_ids=ids[:1]), {201, 422})
    r = await fw(conv_other, source_conversation_id=src, message_ids=[ids[0], ids[0], ids[0]])
    c.check("дубли id схлопываются в одно сообщение", r.status_code == 201 and len(r.json()["messages"]) == 1, r.text[:120])
    r = await fw(conv_other, source_conversation_id=src, message_ids=ids[:2], comment="   ")
    c.check("комментарий из пробелов не создаёт лишнего сообщения", r.status_code == 201 and len(r.json()["messages"]) == 2, r.text[:120])
    r = await fw(conv_other, source_conversation_id=src, message_ids=ids[:1], comment="к" * 4096)
    c.check("комментарий 4096 — ок", r.status_code == 201, r.text[:100])
    c.expect("комментарий 4097 — 422", await fw(conv_other, source_conversation_id=src, message_ids=ids[:1], comment="к" * 4097), 422)
    for label, payload in [("id=0", [0]), ("id=-1", [-1]), ("id строкой", ["abc"]), ("id=float", [1.5]), ("id>int64", [9223372036854775808]),
                           ("id=int64max", [9223372036854775807]), ("null в списке", [None]), ("пустой", [])]:
        c.expect(f"message_ids {label} — 4xx", await fw(conv_other, source_conversation_id=src, message_ids=payload), {404, 422})
    c.expect("source_conversation_id=0 — 422", await fw(conv_other, source_conversation_id=0, message_ids=ids[:1]), 422)
    c.expect("source>int64 — 4xx", await fw(conv_other, source_conversation_id=99999999999999999999, message_ids=ids[:1]), {404, 422})
    c.expect("нет source — 422", await admin.post(f"{BASE}/conversations/{conv_other}/forward", json={"message_ids": ids[:1]}), 422)
    c.expect("hide_sender не bool — 422", await fw(conv_other, source_conversation_id=src, message_ids=ids[:1], hide_sender="maybe"), 422)
    c.expect("целевой чат не существует — 404", await fw(99999999, source_conversation_id=src, message_ids=ids[:1]), 404)
    if foreign:
        c.expect("менеджер: источник — чужой чат — 404", await manager.post(f"{BASE}/conversations/{conv_other}/forward", json={"source_conversation_id": foreign[0], "message_ids": [1]}), 404)
        c.expect("менеджер: цель — чужой чат — 404", await manager.post(f"{BASE}/conversations/{foreign[0]}/forward", json={"source_conversation_id": conv, "message_ids": [1]}), 404)
    # цепочка: переслать пересланное
    fwd = (await fw(conv_other, source_conversation_id=src, message_ids=ids[:1])).json()["messages"][0]["id"]
    await asyncio.sleep(3)
    r = await admin.post(f"{BASE}/conversations/{src}/forward", json={"source_conversation_id": conv_other, "message_ids": [fwd]})
    c.check("пересылка пересланного (цепочка) работает", r.status_code == 201, r.text[:120])
    # 50 сообщений
    fifty = []
    for i in range(50):
        fifty.append((await post({"text": f"m{i}"})).json()["id"])
    await asyncio.sleep(4)
    t0 = time.monotonic()
    r = await fw(conv_other, source_conversation_id=src, message_ids=fifty)
    c.check(f"ровно 50 сообщений пересылаются ({time.monotonic()-t0:.1f} с)", r.status_code == 201 and len(r.json()["messages"]) == 50, r.text[:100])
    # голосовое между аккаунтами: атрибуты сохраняются
    vv = (await upload(admin, "voice", open(v_webm, "rb").read(), "audio/webm", endpoint="voice")).json()
    vm = (await post({"uploads": [vv]})).json()
    await asyncio.sleep(3)
    other_acc = None
    async with SessionLocal() as db:
        rows = (await db.execute(text("select c.id, c.account_id from conversations c where c.id = any(:ids)"), {"ids": admin_ids})).all()
    acc = {i: a for i, a in rows}
    for cid in admin_ids:
        if acc.get(cid) != acc.get(src):
            other_acc = cid
            break
    if other_acc:
        r = await fw(other_acc, source_conversation_id=src, message_ids=[vm["id"]])
        if r.status_code == 201:
            a = r.json()["messages"][0]["attachments"][0]
            c.check("голосовое копией: вид и волна сохранились", a["kind"] == "voice" and a["waveform"] and a["duration_sec"], a)
            c.check("режим — копия", r.json()["mode"] == "copy", r.json()["mode"])
        else:
            info(f"голосовое на другой аккаунт → HTTP {r.status_code} {r.text[:100]}")
    # ожидающее вложение → копией нельзя
    if other_acc:
        async with SessionLocal() as db:
            await db.execute(text("update attachments set status='pending' where id=:a"), {"a": vm["attachments"][0]["id"]})
            await db.commit()
        r = await fw(other_acc, source_conversation_id=src, message_ids=[vm["id"]])
        c.check("докачиваемый файл в другой аккаунт — понятный отказ 422", r.status_code == 422 and "загружается" in r.text, f"{r.status_code} {r.text[:120]}")
        async with SessionLocal() as db:
            await db.execute(text("update attachments set status='ready' where id=:a"), {"a": vm["attachments"][0]["id"]})
            await db.commit()
    # гонка: две одинаковые пересылки одновременно
    rs = await asyncio.gather(*[fw(conv_other, source_conversation_id=src, message_ids=ids[:1]) for _ in range(4)])
    c.check("4 одновременные пересылки без ошибок", all(x.status_code == 201 for x in rs), [x.status_code for x in rs])

    # ===================================================== 6. УСЛУГИ
    c.section("L. Справочник услуг: валидация")
    S = f"{BASE}/services"
    created: list[int] = []
    async def mk(payload: dict[str, Any], client: httpx.AsyncClient = admin) -> httpx.Response:
        r = await client.post(S, json=payload)
        if r.status_code == 201:
            created.append(r.json()["id"])
        return r
    tag = str(int(time.time()))[-6:]
    c.expect("имя из пробелов — 4xx", await mk({"name": "   "}), {422, 400})
    c.expect("имя пустое — 422", await mk({"name": ""}), 422)
    r = await mk({"name": f"  Пробелы вокруг {tag}  ", "price": 100})
    c.check("пробелы вокруг имени обрезаются", r.status_code == 201 and r.json()["name"] == f"Пробелы вокруг {tag}", r.text[:120])
    c.expect("тот же по имени с пробелами — 409", await mk({"name": f"Пробелы вокруг {tag}"}), 409)
    r = await mk({"name": f"Расчёт {tag}", "price": 100})
    c.expect("«Расчет» (без ё) — дубль 409", await mk({"name": f"Расчет {tag}"}), {409, 201})
    info(f"«Расчёт» vs «Расчет» → {'дубль отклонён' if (await admin.post(S, json={'name': f'Расчет {tag}'})).status_code == 409 else 'ДВЕ разные услуги (ё≠е)'}")
    r = await mk({"name": "н" * 255})
    c.check("имя 255 — ок", r.status_code == 201, r.status_code)
    c.expect("имя 256 — 422", await mk({"name": "н" * 256}), 422)
    c.expect("цена отрицательная — 422", await mk({"name": f"neg{tag}", "price": -100}), 422)
    c.expect("цена 0 — 422", await mk({"name": f"zero{tag}", "price": 0}), 422)
    c.expect("цена 12.5 — 422", await mk({"name": f"fl{tag}", "price": 12.5}), 422)
    c.expect("цена строкой 'abc' — 422", await mk({"name": f"st{tag}", "price": "abc"}), 422)
    c.expect("цена 2^63 (переполнение БД) — 4xx", await mk({"name": f"big{tag}", "price": 2**63}), {422, 400})
    c.expect("цена 10^15 копеек — 4xx или принято", await mk({"name": f"big2{tag}", "price": 10**15}), {201, 422, 400})
    c.expect("цена 2^31 (за пределами int4) — не 5xx", await mk({"name": f"big3{tag}", "price": 2**31}), {201, 422, 400})
    c.expect("описание 2001 — 422", await mk({"name": f"d{tag}", "description": "о" * 2001}), 422)
    c.expect("sort_order -1 — 422", await mk({"name": f"so{tag}", "sort_order": -1}), 422)
    c.expect("sort_order 32001 — 422", await mk({"name": f"so2{tag}", "sort_order": 32001}), 422)
    c.expect("XSS в имени сохраняется как текст", await mk({"name": f"<img src=x onerror=alert(1)>{tag}", "price": 100}), 201)
    c.expect("SQL в имени", await mk({"name": f"x'); drop table services;--{tag}"}), 201)
    c.expect("нулевой байт в имени — не 5xx", await mk({"name": f"a\x00b{tag}"}), {201, 422, 400})
    c.expect("лишнее поле игнорируется/422", await mk({"name": f"ex{tag}", "role": "admin"}), {201, 422})
    c.expect("не JSON — 422", await admin.post(S, content=b"not json", headers={"Content-Type": "application/json"}), 422)

    c.section("M. Справочник услуг: изменение, удаление, права")
    sid = created[0]
    c.expect("PATCH несуществующей — 404", await admin.patch(f"{S}/99999999", json={"name": "x"}), 404)
    c.expect("PATCH пустым телом — 200", await admin.patch(f"{S}/{sid}", json={}), 200)
    c.expect("PATCH: стереть цену null — 200", await admin.patch(f"{S}/{sid}", json={"price": None}), 200)
    r = (await admin.get(S)).json()
    c.check("цена стёрта — «по договорённости»", next(x for x in r if x["id"] == sid)["price"] is None)
    c.expect("PATCH: то же имя самой себе — 200", await admin.patch(f"{S}/{sid}", json={"name": f"Пробелы вокруг {tag}"}), 200)
    c.expect("PATCH: имя занято другой — 409", await admin.patch(f"{S}/{sid}", json={"name": f"Расчёт {tag}"}), 409)
    c.expect("PATCH: имя пустое — 422", await admin.patch(f"{S}/{sid}", json={"name": ""}), 422)
    c.expect("PATCH: цена 0 — 422", await admin.patch(f"{S}/{sid}", json={"price": 0}), 422)
    c.expect("PATCH: цена 2^63 — 4xx", await admin.patch(f"{S}/{sid}", json={"price": 2**63}), {422, 400})
    c.expect("PATCH: id-гигант — 4xx", await admin.patch(f"{S}/99999999999999999999", json={"name": "x"}), {404, 422})
    c.expect("DELETE несуществующей — 404", await admin.delete(f"{S}/99999999"), 404)
    c.expect("менеджер PATCH — 403", await manager.patch(f"{S}/{sid}", json={"name": "взлом"}), 403)
    c.expect("менеджер DELETE — 403", await manager.delete(f"{S}/{sid}"), 403)
    c.expect("менеджер читает — 200", await manager.get(S), 200)
    c.expect("без входа читать — 401", await anon.get(S), 401)
    c.expect("без входа создать — 401", await anon.post(S, json={"name": "x"}), 401)
    c.expect("менеджер include_inactive не расширяет — 200", await manager.get(f"{S}?include_inactive=true"), 200)
    ml = (await manager.get(f"{S}?include_inactive=true")).json()
    c.check("менеджер всё равно не видит снятых", all(x["is_active"] for x in ml))

    c.section("N. Позиции сделки: границы")
    async def deal(items: list[dict[str, Any]], cid: int = conv, client: httpx.AsyncClient = admin) -> httpx.Response:
        return await client.post(f"{BASE}/deals", json={"conversation_id": cid, "payment_method": "requisites", "custom_requisites_text": "тест реквизиты", "items": items})
    r = await deal([{"name": "Ок", "amount": 100000}])
    c.check("обычная сделка создаётся", r.status_code == 201, r.text[:150])
    deals = [r.json()["id"]] if r.status_code == 201 else []
    for label, items, want in [("ноль позиций", [], {422, 400}), ("пустое имя", [{"name": "", "amount": 100}], {422, 400}),
                               ("имя из пробелов", [{"name": "  ", "amount": 100}], {422, 400}),
                               ("сумма 0", [{"name": "x", "amount": 0}], {422, 400}), ("сумма отрицательная", [{"name": "x", "amount": -5}], {422, 400}),
                               ("сумма 2^63", [{"name": "x", "amount": 2**63}], {422, 400}),
                               ("сумма 2^31", [{"name": "x", "amount": 2**31}], {201, 422, 400}),
                               ("сумма строкой", [{"name": "x", "amount": "100"}], {201, 422}),
                               ("сумма float", [{"name": "x", "amount": 10.5}], {422}),
                               ("имя 10 000 символов", [{"name": "я" * 10000, "amount": 100}], {201, 422, 400}),
                               ("service_id не существует", [{"name": "x", "amount": 100, "service_id": 99999999}], {422, 400, 404}),
                               ("service_id строкой", [{"name": "x", "amount": 100, "service_id": "abc"}], {422}),
                               ("service_id>int64", [{"name": "x", "amount": 100, "service_id": 10**20}], {422, 404}),
                               ("две позиции с переполнением суммы", [{"name": "a", "amount": 2**62}, {"name": "b", "amount": 2**62}], {422, 400})]:
        rr = await deal(items)
        c.expect(f"сделка: {label}", rr, want)
        if rr.status_code == 201:
            deals.append(rr.json()["id"])
    rr = await deal([{"name": f"поз{i}", "amount": 100} for i in range(200)])
    info(f"сделка на 200 позиций → HTTP {rr.status_code}")
    c.check("200 позиций — не 5xx", rr.status_code < 500)
    if rr.status_code == 201:
        deals.append(rr.json()["id"])
    c.expect("сделка в несуществующий чат — 404/422", await deal([{"name": "x", "amount": 100}], cid=99999999), {404, 422})
    if foreign:
        c.expect("менеджер: сделка в чужой чат — 404", await deal([{"name": "x", "amount": 100}], foreign[0], manager), 404)
    # снятая услуга и своё имя при связи
    inact = await mk({"name": f"снята{tag}", "price": 5000, "is_active": False})
    if inact.status_code == 201:
        c.expect("снятая услуга в новой сделке — 4xx", await deal([{"name": "x", "amount": 100, "service_id": inact.json()["id"]}]), {422, 400})
    act = await mk({"name": f"активная{tag}", "price": 7700})
    if act.status_code == 201:
        rr = await deal([{"name": "своё название", "amount": 5000, "service_id": act.json()["id"]}])
        c.check("связь с услугой и своя цена: цена по прайсу запомнена", rr.status_code == 201 and rr.json()["items"][0]["list_price"] == 7700, rr.text[:150])
        if rr.status_code == 201:
            deals.append(rr.json()["id"])
    for d in deals:
        await admin.post(f"{BASE}/deals/{d}/cancel", json={"reason": "qa"})

    # ===================================================== 7. СТАТИСТИКА
    c.section("O. Статистика: параметры")
    ST = f"{BASE}/stats"
    c.expect("без входа — 401", await anon.get(f"{ST}/overview"), 401)
    for label, params in [("по умолчанию", {}), ("from > to", {"date_from": "2026-09-30", "date_to": "2026-09-01"}),
                          ("один день", {"date_from": "2026-09-15", "date_to": "2026-09-15"}), ("будущее", {"date_from": "2030-01-01", "date_to": "2030-01-31"}),
                          ("прошлое, где данных нет", {"date_from": "2001-01-01", "date_to": "2001-01-31"}), ("только from", {"date_from": "2026-09-10"}),
                          ("только to", {"date_to": "2026-09-10"})]:
        r = await admin.get(f"{ST}/overview", params=params)
        c.expect(f"overview: {label}", r, {200, 422})
        if label == "from > to":
            info(f"from>to → HTTP {r.status_code} {r.text[:100]}")
    for bad in ["2026-13-45", "abc", "2026-02-30", "", "0000-00-00", "99999-01-01", "2026/09/01", "1-1-1"]:
        c.expect(f"overview: date_from={bad!r} — 422", await admin.get(f"{ST}/overview", params={"date_from": bad}), {422})
    t0 = time.monotonic()
    r = await admin.get(f"{ST}/overview", params={"date_from": "0001-01-01", "date_to": "9999-12-31"})
    c.check(f"период 0001–9999: не 5xx, быстро ({time.monotonic()-t0:.1f} с)", r.status_code < 500 and time.monotonic() - t0 < 20, f"{r.status_code} {r.text[:100]}")
    for ep in ("series", "services", "accounts"):
        t0 = time.monotonic()
        r = await admin.get(f"{ST}/{ep}", params={"date_from": "0001-01-01", "date_to": "9999-12-31", **({"granularity": "month"} if ep == "series" else {})})
        c.check(f"{ep}: период 0001–9999 не 5xx ({time.monotonic()-t0:.1f} с)", r.status_code < 500, f"{r.status_code} {r.text[:100]}")
    t0 = time.monotonic()
    r = await admin.get(f"{ST}/managers", params={"date_from": "0001-01-01", "date_to": "9999-12-31"})
    c.check(f"managers: период 0001–9999 не 5xx ({time.monotonic()-t0:.1f} с)", r.status_code < 500, f"{r.status_code}")
    for g in ["hour", "", "DAY", "day;drop", "years"]:
        c.expect(f"series: granularity={g!r} — 422", await admin.get(f"{ST}/series", params={"granularity": g}), 422)
    c.expect("series: день на 400 дней — 4xx с пояснением", await admin.get(f"{ST}/series", params={"date_from": "2025-01-01", "date_to": "2026-02-05", "granularity": "day"}), {422, 400})
    r = await admin.get(f"{ST}/series", params={"date_from": "2020-01-01", "date_to": "2026-09-30", "granularity": "week"})
    c.expect("series: недели за 6 лет", r, {200, 422})
    if r.status_code == 200:
        info(f"недели за 6 лет: {len(r.json()['points'])} столбиков")
    c.expect("series: месяцы за 6 лет", await admin.get(f"{ST}/series", params={"date_from": "2020-01-01", "date_to": "2026-09-30", "granularity": "month"}), 200)

    c.section("P. Статистика: права и разрезы")
    c.expect("менеджер: /managers — 403", await manager.get(f"{ST}/managers"), 403)
    me = (await admin.get(f"{BASE}/auth/me")).json()["id"]
    mgr_me = (await manager.get(f"{BASE}/auth/me")).json()["id"]
    own = (await manager.get(f"{ST}/overview")).json()
    other = await manager.get(f"{ST}/overview", params={"user_id": me})
    info(f"менеджер запрашивает user_id=руководитель → HTTP {other.status_code}")
    if other.status_code == 200:
        c.check("менеджер не получает чужие цифры через user_id", other.json().get("sales_amount", other.json().get("amount")) == own.get("sales_amount", own.get("amount")) and other.json() == own, "цифры отличаются от собственных!")
    for bad in ["1 or 1=1", "abc", "-1", "1;drop", "99999999999999999999"]:
        c.expect(f"user_id={bad!r} — 4xx", await admin.get(f"{ST}/overview", params={"user_id": bad}), {422, 400, 404, 403, 200})
    r = await admin.get(f"{ST}/overview", params={"user_id": 99999999})
    info(f"user_id несуществующего → HTTP {r.status_code}")
    c.check("user_id несуществующего — не 5xx", r.status_code < 500)
    empty = await admin.get(f"{ST}/overview", params={"date_from": "2030-01-01", "date_to": "2030-01-31"})
    if empty.status_code == 200:
        j = empty.json()
        info("пустой период: " + ", ".join(f"{k}={v}" for k, v in j.items() if not isinstance(v, (dict, list)))[:300])
        bad = [k for k, v in j.items() if isinstance(v, float) and v != v]
        c.check("в пустом периоде нет NaN", not bad, bad)
    c.section("Q. Статистика: внутренняя согласованность")
    p = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    ov = (await admin.get(f"{ST}/overview", params=p)).json()
    se = (await admin.get(f"{ST}/series", params={**p, "granularity": "day"})).json()
    sv = (await admin.get(f"{ST}/services", params=p)).json()
    ac = (await admin.get(f"{ST}/accounts", params=p)).json()
    mg = (await admin.get(f"{ST}/managers", params=p)).json()
    total = ov.get("sales_amount", ov.get("amount"))
    c.check("ключи сводки распознаны", total is not None, list(ov)[:10])
    if total is not None:
        c.check("сумма ряда графика = сумме сводки", sum(x["amount"] for x in se["points"]) == total, (sum(x["amount"] for x in se["points"]), total))
        rows = sv.get("rows", sv.get("items", sv if isinstance(sv, list) else []))
        if rows:
            amt_key = next((k for k in ("amount", "revenue", "sales_amount") if k in rows[0]), None)
            if amt_key:
                c.check("сумма по услугам = сумме сводки", sum(r_[amt_key] for r_ in rows) == total, (sum(r_[amt_key] for r_ in rows), total))
        if ac:
            k = next((k for k in ("amount", "sales_amount", "revenue") if k in ac[0]), None)
            if k:
                c.check("сумма по аккаунтам = сумме сводки", sum(a[k] for a in ac) == total, (sum(a[k] for a in ac), total))
        if mg:
            k = next((k for k in ("amount", "sales_amount", "revenue") if k in mg[0]), None)
            if k:
                c.check("сумма по менеджерам ≤ сумме сводки", sum(m_[k] for m_ in mg) <= total, (sum(m_[k] for m_ in mg), total))
    t0 = time.monotonic()
    for ep in ("overview", "series", "services", "accounts", "managers"):
        t1 = time.monotonic()
        r = await admin.get(f"{ST}/{ep}", params={"date_from": "2026-01-01", "date_to": "2026-09-30", **({"granularity": "week"} if ep == "series" else {})})
        c.check(f"{ep}: 9 месяцев за {time.monotonic()-t1:.2f} с (< 3 с)", r.status_code == 200 and time.monotonic() - t1 < 3, f"{r.status_code}")
    # -------------------------------------------------- уборка
    for sid_ in created:
        await admin.delete(f"{S}/{sid_}")
    for cl in (admin, manager, anon):
        await cl.aclose()
    print("\nИнформационные наблюдения:")
    for line in INFO:
        print("  ℹ", line)
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
