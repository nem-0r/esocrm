"""Картинка QR-кода для входа в Telegram.

SVG собираем сами, а не берём готовую фабрику из библиотеки, по двум причинам.
Первая: под модулями нужен непрозрачный белый фон — в тёмной теме прозрачный
QR не считывается камерой. Вторая: соседние модули в строке склеиваются в один
прямоугольник, иначе на плотном коде получается около двух тысяч элементов и
картинка весит втрое больше, чем нужно.
"""

import base64

import qrcode

# Запас на случай, если камера прихватит часть фона: с уровнем M код читается,
# даже когда до четверти площади засвечено или перекрыто.
_ERROR_CORRECTION = qrcode.constants.ERROR_CORRECT_M
# Тихая зона вокруг кода. Меньше четырёх модулей — часть сканеров не находит код.
_BORDER = 4


def login_qr_data_uri(payload: str) -> str:
    """QR как `data:`-ссылка, готовая подставиться в `<img src=…>`.

    Отдаём именно ссылку, а не голый текст токена: рисование на стороне браузера
    потребовало бы отдельной библиотеки в сборке, а картинку с сервера нельзя
    испортить ни темой оформления, ни размером экрана.
    """
    code = qrcode.QRCode(error_correction=_ERROR_CORRECTION, border=_BORDER)
    code.add_data(payload)
    code.make(fit=True)
    matrix = code.get_matrix()
    side = len(matrix)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR-код для входа в Telegram">',
        f'<rect width="{side}" height="{side}" fill="#ffffff"/>',
    ]
    for y, row in enumerate(matrix):
        x = 0
        while x < side:
            if not row[x]:
                x += 1
                continue
            run_end = x
            while run_end < side and row[run_end]:
                run_end += 1
            parts.append(
                f'<rect x="{x}" y="{y}" width="{run_end - x}" height="1" fill="#000000"/>'
            )
            x = run_end
    parts.append("</svg>")

    svg = "".join(parts).encode()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
