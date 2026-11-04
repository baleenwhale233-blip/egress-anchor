"""Validate a candidate with only known read-only geodata aliases in a temporary directory."""

from pathlib import Path
import subprocess
import tempfile


def validate_candidate(candidate, resources, prefix="p0-native-"):
    aliases = {"GEOIP.dat": ("GeoIP.dat", "geoip.dat"),
               "GEOSITE.dat": ("GeoSite.dat", "geosite.dat"),
               "ASN.mmdb": ("ASN.mmdb",),
               "GEOIP.metadb": ("Country.mmdb", "country.mmdb")}
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        root = Path(directory)
        for source, names in aliases.items():
            path = resources / source
            if path.is_file():
                for name in names:
                    alias = root / name
                    if not alias.exists():
                        alias.symlink_to(path.resolve())
        temporary = root / "candidate.yaml"
        temporary.write_text(candidate)
        checked = subprocess.run(["/usr/bin/verge-mihomo", "-t", "-d", directory, "-f", str(temporary)],
                                 capture_output=True, timeout=8)
        if checked.returncode:
            raise ValueError("pinned validation core rejected the native fixture candidate")
