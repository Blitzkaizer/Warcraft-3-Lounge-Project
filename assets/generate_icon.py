"""
Generates high-resolution multi-size Warcraft III Lounge icon.
Includes 16x16, 32x32, 48x48, 64x64, 128x128, and 256x256 resolutions.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def create_lounge_icon(size: int = 256) -> Image.Image:
    # High-res canvas with anti-aliasing (render at 2x and downsample)
    s = size * 2
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Outer Shield / Crest (Deep Warcraft Dark Navy / Slate)
    pad = int(s * 0.05)
    outer_box = [pad, pad, s - pad, s - pad]

    # Radial/concentric background ring
    d.ellipse(outer_box, fill="#0d1117", outline="#f59e0b", width=int(s * 0.035))

    # Inner circular disc with gradient effect
    inner_pad = int(s * 0.09)
    d.ellipse([inner_pad, inner_pad, s - inner_pad, s - inner_pad], fill="#1e293b", outline="#d97706", width=int(s * 0.02))

    # Subtle decorative crossed swords (accents)
    mid = s // 2
    sw_len = int(s * 0.32)
    sw_w = max(2, int(s * 0.025))

    # Sword 1
    d.line([mid - sw_len, mid - sw_len, mid + sw_len, mid + sw_len], fill="#64748b", width=sw_w)
    # Sword 2
    d.line([mid + sw_len, mid - sw_len, mid - sw_len, mid + sw_len], fill="#64748b", width=sw_w)

    # Center disc for text legibility
    center_pad = int(s * 0.20)
    d.ellipse([center_pad, center_pad, s - center_pad, s - center_pad], fill="#0f172a", outline="#fbbf24", width=int(s * 0.018))

    # Bold "W3" Text in Gold
    font_size = int(s * 0.32)
    font = None
    for font_name in ["arialbd.ttf", "segoeuib.ttf", "impact.ttf", "arial.ttf"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except Exception:
            pass

    if font is None:
        font = ImageFont.load_default()

    text = "W3"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (s - tw) // 2 - bbox[0]
    ty = (s - th) // 2 - bbox[1]

    # Text drop shadow
    d.text((tx + int(s * 0.015), ty + int(s * 0.015)), text, fill="#451a03", font=font)
    # Text highlight
    d.text((tx, ty), text, fill="#f59e0b", font=font)

    # Downsample cleanly with Lanczos
    return img.resize((size, size), Image.Resampling.LANCZOS)

def main():
    assets_dir = Path(__file__).parent
    icon_png_path = assets_dir / "icon.png"
    icon_ico_path = assets_dir / "icon.ico"
    tray_png_path = assets_dir / "tray_icon.png"

    # Generate primary master image
    master_img = create_lounge_icon(256)
    master_img.save(icon_png_path, format="PNG")
    print(f"Saved master icon: {icon_png_path}")

    # Generate multi-resolution icon sizes for Windows
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon_layers = [create_lounge_icon(sz[0]) for sz in sizes]

    # Save .ico containing all resolutions
    master_img.save(
        icon_ico_path,
        format="ICO",
        sizes=sizes,
        append_images=icon_layers[1:]
    )
    print(f"Saved multi-resolution Windows ICO: {icon_ico_path}")

    # Also update tray icon (64x64)
    tray_img = create_lounge_icon(64)
    tray_img.save(tray_png_path, format="PNG")
    print(f"Updated tray icon: {tray_png_path}")

if __name__ == "__main__":
    main()
