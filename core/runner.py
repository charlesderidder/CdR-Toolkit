"""Subprocessen uitvoeren met live output-streaming naar de log."""
import base64
import subprocess

CREATE_NO_WINDOW = 0x08000000  # verberg consolevensters van kindprocessen

# Wordt vóór elk PowerShell-script gezet: UTF-8-uitvoer + geen voortgangsbalken
PS_HEADER = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "$ProgressPreference = 'SilentlyContinue'\n"
)


def run_stream(cmd, on_line, cr_split=False, encoding="utf-8") -> int:
    """
    Voer een commando uit en stream de uitvoer regel voor regel naar on_line(regel).
    cr_split=True behandelt ook \\r als regeleinde (handig voor winget-voortgang).
    Geeft de exitcode terug, of -1 als het proces niet gestart kon worden.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        on_line(f"Programma niet gevonden: {cmd[0]}", "ERROR")
        return -1
    except OSError as exc:
        on_line(f"Starten mislukt: {exc}", "ERROR")
        return -1

    buf = bytearray()
    while True:
        chunk = proc.stdout.read(1)
        if not chunk:
            break
        b = chunk[0]
        if b == 0x0A or (cr_split and b == 0x0D):  # \n (en optioneel \r) = regeleinde
            line = buf.decode(encoding, errors="replace").strip()
            buf.clear()
            if line:
                on_line(line)
        elif b != 0x0D:
            buf.append(b)
    proc.wait()
    if buf:  # laatste regel zonder regeleinde
        line = buf.decode(encoding, errors="replace").strip()
        if line:
            on_line(line)
    return proc.returncode


def run_powershell(script: str, on_line) -> int:
    """Voer een PowerShell-script uit (bypass execution policy, verborgen venster)."""
    encoded = base64.b64encode((PS_HEADER + script).encode("utf-16-le")).decode("ascii")
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive",
           "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    return run_stream(cmd, on_line)


def run_quiet(cmd) -> int:
    """Voer een commando uit zonder output te loggen; geef de exitcode terug."""
    return run_stream(cmd, lambda *a: None)
