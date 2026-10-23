# Warcraft III Lounge Companion

A custom desktop companion application in Python for Windows enabling direct peer-to-peer (P2P) internet play for Classic Warcraft III (`war3.exe` / `Frozen Throne.exe`) without third-party services like GameRanger or Hamachi.

---

## Key Features

- **P2P UDP 6112 LAN Proxy:** Intercepts, wraps, and tunnels Warcraft III local broadcast announcements and connects peers without needing virtual network drivers (TAP/TUN).
- **NAT Traversal (STUN + UDP Hole Punching):** Connects friends across different countries and home routers directly using public STUN resolution.
- **Custom In-App Accounts:** Clean register and login forms with username and password (no Google/external OAuth). Ready for cloud storage (Supabase).
- **Automatic Game Launch:** Detects `war3.exe` via Windows Registry and launches all players into the LAN screen simultaneously.
- **Standalone Windows Distribution:** Pre-compiled standalone executable ready to share via GitHub Releases.

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                                 Host Machine                                      |
|   +-----------------------+              +------------------------------------+   |
|   |  Warcraft III (Host)  |              |      Lounge Companion (Host)       |   |
|   |  - Creates LAN match  |              |  - Custom Username Auth            |   |
|   |  - Listens on TCP 6112|              |  - Resolves STUN external mapping  |   |
|   |  - Auto-launched by   |<-------------+  - Generates shareable Lounge Code |   |
|   |    companion app      |              |  - Intercepts UDP 6112 broadcasts  |   |
|   +-----------+-----------+              +-----------------+------------------+   |
+---------------|--------------------------------------------|----------------------+
                | UDP 6112 (LAN broadcast)                   |                      |
                +------------------------------------------->|                      |
                                                             | P2P Tunnel (UDP/TCP) |
                                                             | (Hole-Punched Socket)|
                                                             v                      |
+-----------------------------------------------------------------------------------+
|                                Client Machine                                     |
|   +-----------------------+              +------------------------------------+   |
|   | Warcraft III (Client) |              |      Lounge Companion (Client)     |   |
|   |  - In LAN Lobby Screen|              |  - Custom Username Auth            |   |
|   |  - Auto-launched on   |<-------------+  - Decodes Lounge Code             |   |
|   |    Host 'Launch' cmd  |              |  - Punches router NAT to Host      |   |
|   |  - Connects 127.0.0.1 |              |  - Injects UDP 6112 to 127.0.0.1   |   |
|   |    port 6112          |              |  - Bridges TCP 6112 to Host        |   |
|   +-----------------------+              +------------------------------------+   |
+-----------------------------------------------------------------------------------+
```

---

## Project Structure

```
Warcraft 3 Lounge Project/
├── main.py                     # Primary entry point (GUI or CLI)
├── build.py                    # PyInstaller standalone executable builder
├── requirements.txt            # Python dependencies
├── README.md                   # Documentation & guide
├── dist/                       # Compiled distribution builds
│   ├── Warcraft3Lounge/        # Standalone application folder
│   │   └── Warcraft3Lounge.exe # Portable Windows executable
│   └── Warcraft3Lounge-v1.0.0-windows-x64.zip
├── src/
│   ├── __init__.py
│   ├── auth/                   # Custom username/password account system
│   │   ├── __init__.py
│   │   └── auth_manager.py     # Session persistence & Supabase REST client
│   ├── core/                   # Core Networking & Protocol Interception
│   │   ├── __init__.py
│   │   ├── config.py           # Constants, Classic WC3 registry paths, port defaults
│   │   ├── packet_parser.py    # W3GS protocol parser (0xF7 header & game info)
│   │   ├── udp_proxy.py        # Windows UDP listener & broadcast injector
│   │   └── tcp_tunnel.py       # Bi-directional TCP stream proxy for lobby sync
│   ├── network/                # NAT Traversal & Lounge Room System
│   │   ├── __init__.py
│   │   ├── stun.py             # Pure-Python RFC 5389 STUN resolver
│   │   ├── lounge_code.py      # Base32 offline invite code encoder/decoder
│   │   ├── protocol.py         # Tunnel packet framing (Magic 0x5733)
│   │   ├── nat_puncher.py      # UDP hole puncher & keep-alive engine
│   │   └── lounge_session.py   # High-level Host and Client coordinator
│   ├── launcher/               # Auto-launching Classic war3.exe
│   │   ├── __init__.py
│   │   └── game_launcher.py    # Windows Registry auto-detection & process execution
│   └── ui/                     # Modern Dark Desktop UI (CustomTkinter)
│       ├── __init__.py
│       ├── app.py              # Application window lifecycle & switching
│       ├── auth_frame.py       # Login & Register views
│       └── lounge_frame.py     # Main Lounge Dashboard
└── tests/
    ├── __init__.py
    ├── test_packet_parser.py   # W3GS packet parsing & validation tests
    ├── test_network.py         # STUN, LoungeCode & protocol framing tests
    └── test_launcher_and_auth.py # Auto-launcher registry scan & Auth flow tests
```

---

## How to Run

### Option 1: Run the Standalone `.exe`
Double-click `dist\Warcraft3Lounge\Warcraft3Lounge.exe`. No Python installation is needed!

### Option 2: Run from Python Source
```powershell
python main.py
```

### Option 3: Command Line Mode
```powershell
# Host a Lounge
python main.py --mode host --username Arthas --auto-launch

# Join a Lounge
python main.py --mode join --code W3L-ONQQ-25AX-4FLTG --username Jaina --auto-launch

# Run automated tests
python -m unittest discover tests
```

---

## Distributing to Friends via GitHub Releases
1. Take the generated zip archive: `dist\Warcraft3Lounge-v1.0.0-windows-x64.zip`.
2. Go to your repository on GitHub -> **Releases** -> **Draft a new release**.
3. Attach `Warcraft3Lounge-v1.0.0-windows-x64.zip` as a binary asset and publish.
4. Friends download the zip, extract it, and run `Warcraft3Lounge.exe`!
