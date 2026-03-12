from pathlib import Path
import qrcode


def make_qr(text: str, output_png: Path) -> None:
    img = qrcode.make(text)
    img.save(output_png)
