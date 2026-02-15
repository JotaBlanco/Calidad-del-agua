"""
SINAC Water Quality Scraper

Scrapes water quality analysis PDFs from SINAC (Sistema de Información
Nacional de Aguas de Consumo) for Spanish municipalities.

Usage:
    python scrape.py --ccaa 12 --provincia 36 --municipio 36041
"""

import argparse
import io
import re
import time
from pathlib import Path

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sinac.sanidad.gob.es/CiudadanoWeb/ciudadano"
DATA_DIR = Path(__file__).parent.parent / "data"
PDF_DIR = DATA_DIR / "raw" / "pdfs"
CSV_DIR = DATA_DIR / "raw" / "csvs"

# Map of analysis type keywords found in <legend> to short names for filenames
ANALYSIS_TYPE_MAP = {
    "control": "Control",
    "completo": "Completo",
    "radiactividad": "Radiactividad",
    "vigilancia": "Vigilancia",
}


def create_session():
    """Create a requests session and load the SINAC main page."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (water-quality-research)"
    })
    session.get(f"{BASE_URL}/informacionAbastecimientoActionEntrada.do")
    return session


def get_provinces(session, ccaa_code):
    """Get provinces for a given autonomous community."""
    r = session.get(
        f"{BASE_URL}/cargarComboProvinciasAction.do",
        params={"id": str(ccaa_code)},
    )
    soup = BeautifulSoup(r.text, "html.parser")
    return {
        opt["value"]: opt.text.strip()
        for opt in soup.find_all("option")
        if opt.get("value")
    }


def get_municipalities(session, provincia_code):
    """Get municipalities for a given province."""
    r = session.get(
        f"{BASE_URL}/cargarComboMunicipiosAction.do",
        params={"id": str(provincia_code)},
    )
    soup = BeautifulSoup(r.text, "html.parser")
    return {
        opt["value"]: opt.text.strip()
        for opt in soup.find_all("option")
        if opt.get("value")
    }


def get_distribution_networks(session, ccaa_code, provincia_code, municipio_code):
    """Submit search and get distribution networks for a municipality."""
    r = session.post(f"{BASE_URL}/informacionRedes.do", data={
        "codComunidad": str(ccaa_code),
        "codProvincia": str(provincia_code),
        "codMunicipio": str(municipio_code),
        "method": "Buscar",
    })
    soup = BeautifulSoup(r.text, "html.parser")

    networks = {}
    for a in soup.find_all("a", href=re.compile(r"eleccionRedDistribucion")):
        match = re.search(r"eleccionRedDistribucion\((\d+)\)", a["href"])
        if match:
            network_id = match.group(1)
            network_name = a.text.strip()
            networks[network_id] = network_name

    municipio_name = ""
    inp = soup.find("input", {"name": "denMunicipio"})
    if inp:
        municipio_name = inp.get("value", "")

    return networks, municipio_name


def get_boletins(session, provincia_code, municipio_code, municipio_name, network_id):
    """Navigate to a distribution network detail page and extract boletin info."""
    r = session.post(
        f"{BASE_URL}/informacionAbastecimientoActionDetalleRed.do",
        data={
            "codMunicipio": str(municipio_code),
            "codProvincia": str(provincia_code),
            "denMunicipio": municipio_name,
            "idRed": str(network_id),
        },
    )
    soup = BeautifulSoup(r.text, "html.parser")

    # Get the network name from hidden input
    network_name = ""
    inp = soup.find("input", {"name": "denRed"})
    if inp:
        network_name = inp.get("value", "")

    boletins = []

    # Each analysis section is a <fieldset class="bloqueFieldsetU"> with a <legend>
    for fieldset in soup.find_all("fieldset", class_="bloqueFieldsetU"):
        legend = fieldset.find("legend")
        if not legend:
            continue

        legend_text = legend.get_text(strip=True)

        # Determine analysis type
        analysis_type = "Otro"
        for keyword, type_name in ANALYSIS_TYPE_MAP.items():
            if keyword in legend_text.lower():
                analysis_type = type_name
                break

        # Find all download links in this fieldset
        for a in fieldset.find_all("a", href=re.compile(r"descargaBoletin")):
            boletin_id = re.search(r"descargaBoletin\((\d+)\)", a["href"]).group(1)
            tr = a.find_parent("tr")
            date_str = ""
            qualification = ""
            if tr:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    date_str = cells[0]
                    qualification = cells[1]

            boletins.append({
                "boletin_id": boletin_id,
                "date": date_str,
                "qualification": qualification,
                "analysis_type": analysis_type,
                "network_name": network_name,
            })

    return boletins, network_name


def download_pdf(session, provincia_code, municipio_code, municipio_name,
                 network_id, network_name, boletin_id):
    """Download a boletin PDF."""
    r = session.post(f"{BASE_URL}/descargaBoletin.do", data={
        "codMunicipio": str(municipio_code),
        "codProvincia": str(provincia_code),
        "idBoletin": str(boletin_id),
        "idRed": str(network_id),
        "denMunicipio": municipio_name,
        "denRed": network_name,
    })

    if r.status_code == 200 and r.content[:5] == b"%PDF-":
        return r.content
    else:
        print(f"  WARNING: Failed to download boletin {boletin_id} "
              f"(status={r.status_code}, first bytes={r.content[:20]})")
        return None


def parse_pdf(pdf_bytes):
    """Extract structured data from a boletin PDF.

    Returns:
        metadata: dict with header info (red, municipio, fecha, tipo, etc.)
        parameters: list of dicts with parameter measurements
    """
    metadata = {}
    parameters = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    lines = full_text.split("\n")

    # Parse header metadata
    header_fields = {
        "Red": "red",
        "Punto de muestreo": "punto_muestreo",
        "Municipio": "municipio",
        "Zona abastecimiento": "zona_abastecimiento",
        "Fecha de toma": "fecha_toma",
        "Tipo de Boletin": "tipo_boletin",
        "Tipo de analisis": "tipo_analisis",
        "Laboratorio/s": "laboratorio",
        "Calificacion de la muestra": "calificacion",
    }

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check for header fields
        for field_prefix, field_key in header_fields.items():
            if line.startswith(field_prefix):
                value = line[len(field_prefix):].strip()
                # Some fields span multiple lines (e.g. "Punto de muestreo")
                while (i + 1 < len(lines)
                       and not any(lines[i + 1].strip().startswith(fp) for fp in header_fields)
                       and not lines[i + 1].strip().startswith("Parametros")
                       and not lines[i + 1].strip().startswith("Recomendaciones")
                       and not lines[i + 1].strip().startswith("Incidencias")
                       and lines[i + 1].strip()
                       and not re.match(r"^.+\s+[\d.,]+\s+\S+", lines[i + 1].strip())):
                    i += 1
                    value += " " + lines[i].strip()
                metadata[field_key] = value
                break

        # Check for parameter lines: "Name Value Unit"
        # Match the LAST number+unit pair, so names like "Suma 4 Trihalometanos"
        # are captured correctly. The unit is always at the end after the last number.
        param_match = re.match(
            r"^(.+)\s+([\d.,]+)\s+(\S+(?:\s+\S+)*?)\s*$",
            line,
        )
        if param_match:
            name = param_match.group(1).strip()
            # Skip header rows and page footers
            if name in ("Denominación", "Boletin") or name.startswith("Página"):
                i += 1
                continue
            # Skip if it looks like a metadata line
            if any(name.startswith(fp) for fp in header_fields):
                i += 1
                continue
            # Skip section headers
            if name.startswith("Parametros"):
                i += 1
                continue

            parameters.append({
                "parametro": name,
                "valor": param_match.group(2),
                "unidad": param_match.group(3),
            })

        i += 1

    return metadata, parameters


def sanitize_filename(name):
    """Make a string safe for use in filenames."""
    # Replace slashes and other problematic chars
    name = re.sub(r"[/\\:*?\"<>|]", "_", name)
    # Collapse multiple underscores/spaces
    name = re.sub(r"[_\s]+", "_", name)
    return name.strip("_")


def scrape_municipality(ccaa_code, provincia_code, municipio_code):
    """Scrape all water quality data for a municipality."""
    print(f"Starting scrape for CCAA={ccaa_code}, Provincia={provincia_code}, "
          f"Municipio={municipio_code}")

    session = create_session()

    # Load dropdowns (needed for session state)
    get_provinces(session, ccaa_code)
    get_municipalities(session, provincia_code)

    # Get distribution networks
    networks, municipio_name = get_distribution_networks(
        session, ccaa_code, provincia_code, municipio_code
    )
    print(f"Municipality: {municipio_name}")
    print(f"Found {len(networks)} distribution networks")

    all_records = []

    for network_id, network_display_name in networks.items():
        print(f"\n--- Network: {network_display_name} (id={network_id}) ---")

        boletins, network_name = get_boletins(
            session, provincia_code, municipio_code, municipio_name, network_id
        )
        print(f"  Found {len(boletins)} boletins")

        for boletin in boletins:
            bid = boletin["boletin_id"]
            date_str = boletin["date"]
            analysis_type = boletin["analysis_type"]

            print(f"  Downloading boletin {bid} ({analysis_type}, {date_str})...")

            pdf_bytes = download_pdf(
                session, provincia_code, municipio_code, municipio_name,
                network_id, network_name, bid,
            )

            if pdf_bytes is None:
                continue

            # Build PDF filename: YYYYMMDD_Municipio_Network_Type.pdf
            # Parse date from dd/mm/yyyy format
            date_clean = date_str.split(" ")[0]  # Remove time if present
            try:
                parts = date_clean.split("/")
                date_prefix = f"{parts[2]}{parts[1]}{parts[0]}"
            except (IndexError, ValueError):
                date_prefix = date_clean.replace("/", "")

            safe_municipio = sanitize_filename(municipio_name)
            safe_network = sanitize_filename(network_name)

            pdf_filename = f"{date_prefix}_{safe_municipio}_{safe_network}_{analysis_type}.pdf"
            pdf_path = PDF_DIR / pdf_filename
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(pdf_bytes)
            print(f"    Saved PDF: {pdf_filename}")

            # Parse PDF content
            metadata, parameters = parse_pdf(pdf_bytes)

            if not parameters:
                print(f"    WARNING: No parameters extracted from PDF")
                continue

            # Build CSV records
            for param in parameters:
                record = {
                    "municipio": municipio_name,
                    "red": network_name,
                    "punto_muestreo": metadata.get("punto_muestreo", ""),
                    "zona_abastecimiento": metadata.get("zona_abastecimiento", ""),
                    "fecha_toma": metadata.get("fecha_toma", ""),
                    "tipo_boletin": metadata.get("tipo_boletin", ""),
                    "tipo_analisis": metadata.get("tipo_analisis", ""),
                    "laboratorio": metadata.get("laboratorio", ""),
                    "calificacion": metadata.get("calificacion", ""),
                    "boletin_id": bid,
                    "parametro": param["parametro"],
                    "valor": param["valor"],
                    "unidad": param["unidad"],
                }
                all_records.append(record)

            # Save per-boletin CSV
            csv_filename = f"{date_prefix}_{safe_municipio}_{safe_network}_{analysis_type}.csv"
            csv_path = CSV_DIR / csv_filename
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            df_boletin = pd.DataFrame([
                {**metadata, **param} for param in parameters
            ])
            df_boletin.to_csv(csv_path, index=False)
            print(f"    Saved CSV: {csv_filename} ({len(parameters)} parameters)")

            # Be polite to the server
            time.sleep(0.5)

    # Save combined CSV for the municipality
    if all_records:
        combined_path = CSV_DIR / f"{safe_municipio}_all.csv"
        df_all = pd.DataFrame(all_records)
        df_all.to_csv(combined_path, index=False)
        print(f"\nSaved combined CSV: {combined_path.name} ({len(all_records)} total records)")

    print(f"\nDone! Scraped {len(all_records)} parameter measurements.")
    return all_records


def main():
    parser = argparse.ArgumentParser(description="Scrape SINAC water quality data")
    parser.add_argument("--ccaa", type=int, required=True, help="CCAA code (e.g., 12 for Galicia)")
    parser.add_argument("--provincia", type=int, required=True, help="Province code (e.g., 36 for Pontevedra)")
    parser.add_argument("--municipio", type=int, required=True, help="Municipality code (e.g., 36041 for Poio)")
    args = parser.parse_args()

    scrape_municipality(args.ccaa, args.provincia, args.municipio)


if __name__ == "__main__":
    main()
