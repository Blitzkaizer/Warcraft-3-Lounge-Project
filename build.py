"""
PyInstaller build script to package Warcraft III Lounge into a standalone Windows .exe
Includes all CustomTkinter theme assets, fonts, and dependencies.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import customtkinter

def build_exe():
    print("Building Warcraft III Lounge Standalone Executables...")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "Warcraft3Lounge.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["taskkill", "/F", "/IM", "Warcraft3Lounge_Portable.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    ctk_path = os.path.dirname(customtkinter.__file__)
    add_data_arg = f"--add-data={ctk_path}{os.pathsep}customtkinter"
    add_assets_arg = f"--add-data=assets{os.pathsep}assets"
    icon_arg = "--icon=assets/icon.ico"

    # 1. Build Onedir (Fast startup, release folder)
    cmd_onedir = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=Warcraft3Lounge",
        icon_arg,
        "--clean",
        add_data_arg,
        add_assets_arg,
        "--collect-all=customtkinter",
        "--collect-all=pystray",
        "--collect-all=PIL",
        "main.py"
    ]
    print("Building onedir version...")
    subprocess.run(cmd_onedir, check=True)

    # Ensure assets are directly copied to onedir
    dst_assets = Path("dist") / "Warcraft3Lounge" / "_internal" / "customtkinter" / "assets"
    src_assets = Path(ctk_path) / "assets"
    if src_assets.exists():
        dst_assets.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_assets, dst_assets, dirs_exist_ok=True)
        print("Verified CustomTkinter assets in onedir folder.")

    dst_app_assets = Path("dist") / "Warcraft3Lounge" / "assets"
    src_app_assets = Path("assets")
    if src_app_assets.exists():
        shutil.copytree(src_app_assets, dst_app_assets, dirs_exist_ok=True)
        print("Verified sound assets in onedir folder.")

    # 2. Build Official Inno Setup Windows Installer
    print("\nBuilding Official Windows Setup Wizard...")
    iscc_candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    iscc_path = None
    for c in iscc_candidates:
        if c.is_file():
            iscc_path = c
            break

    if iscc_path:
        from src.core.config import APP_VERSION
        iss_file = Path("installer") / "installer.iss"
        subprocess.run([str(iscc_path), str(iss_file)], check=True)
        setup_name = f"Warcraft3Lounge_Setup_v{APP_VERSION}.exe"
        zip_name = f"Warcraft3Lounge_Setup_v{APP_VERSION}.zip"
        print(f"Compiled dist\\{setup_name} successfully.")

        # Sign with Authenticode
        cert_script = Path("installer") / "make_cert_and_sign.ps1"
        if cert_script.is_file():
            subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(cert_script)], check=False)

        # Create distribution setup ZIP
        setup_exe = Path("dist") / setup_name
        setup_zip = Path("dist") / zip_name
        if setup_exe.is_file():
            import zipfile
            with zipfile.ZipFile(setup_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(setup_exe, arcname=setup_name)
            print(f"Created Setup Distribution ZIP: {setup_zip.name}")
    else:
        print("Warning: ISCC.exe not found. Run installer\\build_installer.ps1 after installing Inno Setup.")

    print("\n=======================================================")
    print("  OFFICIAL INSTALLER BUILD COMPLETE!")
    print(f"  1. Setup Wizard: dist\\{setup_name}")
    print(f"  2. Setup ZIP:    dist\\{zip_name}")
    print("=======================================================\n")

if __name__ == "__main__":
    build_exe()
