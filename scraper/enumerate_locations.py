"""
Enumerate all CCAA, provinces, and municipalities available in SINAC.
Saves the full catalog to a CSV for reference.
"""

import time
import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sinac.sanidad.gob.es/CiudadanoWeb/ciudadano"


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (water-quality-research)"})

    # Load main page
    r = session.get(f"{BASE_URL}/informacionAbastecimientoActionEntrada.do")
    soup = BeautifulSoup(r.text, "html.parser")

    # Get all CCAA
    ccaa_select = soup.find("select", {"name": "codComunidad"})
    ccaa_list = []
    for opt in ccaa_select.find_all("option"):
        val = opt.get("value", "")
        if val:
            ccaa_list.append({"ccaa_code": val, "ccaa_name": opt.text.strip()})

    print(f"Found {len(ccaa_list)} CCAA")

    all_locations = []

    for ccaa in ccaa_list:
        print(f"\n--- {ccaa['ccaa_name']} ---")

        # Get provinces
        r = session.get(f"{BASE_URL}/cargarComboProvinciasAction.do",
                        params={"id": ccaa["ccaa_code"]})
        soup = BeautifulSoup(r.text, "html.parser")
        provinces = []
        for opt in soup.find_all("option"):
            val = opt.get("value", "")
            if val:
                provinces.append({"prov_code": val, "prov_name": opt.text.strip()})

        for prov in provinces:
            # Get municipalities
            r = session.get(f"{BASE_URL}/cargarComboMunicipiosAction.do",
                            params={"id": prov["prov_code"]})
            soup = BeautifulSoup(r.text, "html.parser")

            muni_count = 0
            for opt in soup.find_all("option"):
                val = opt.get("value", "")
                if val:
                    all_locations.append({
                        "ccaa_code": ccaa["ccaa_code"],
                        "ccaa_name": ccaa["ccaa_name"],
                        "provincia_code": prov["prov_code"],
                        "provincia_name": prov["prov_name"],
                        "municipio_code": val,
                        "municipio_name": opt.text.strip(),
                    })
                    muni_count += 1

            print(f"  {prov['prov_name']}: {muni_count} municipalities")
            time.sleep(0.2)

    # Save catalog
    df = pd.DataFrame(all_locations)
    out_path = "/Users/javiquix/Desktop/Personal/Calidad-del-agua/data/locations_catalog.csv"
    df.to_csv(out_path, index=False)
    print(f"\nTotal: {len(all_locations)} municipalities across {len(ccaa_list)} CCAA")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
