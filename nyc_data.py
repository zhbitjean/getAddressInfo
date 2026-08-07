from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from building_classes import format_building_class


GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
PLUTO_URL = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
HPD_BUILDINGS_URL = "https://data.cityofnewyork.us/resource/kj4p-ruqc.json"
BUILDING_FOOTPRINTS_URL = "https://data.cityofnewyork.us/resource/5zhs-2jue.json"
HPD_VIOLATIONS_URL = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
LEGACY_CO_URL = "https://a810-bisweb.nyc.gov/bisweb/COsByLocationServlet"
LEGACY_CO_POST_URL = "https://a810-bisweb.nyc.gov/bisweb/CofoJobDocumentServlet"
LEGACY_CO_CONTENT_URL = "https://a810-bisweb.nyc.gov/bisweb/CofoDocumentContentServlet"
DOB_NOW_CO_DATA_URL = "https://data.cityofnewyork.us/resource/pkdm-hqz6.json"
DOB_NOW_PUBLIC_URL = "https://a810-dobnow.nyc.gov/publish/Index.html#!/"
HPD_ONLINE_URL = "https://hpdonline.nyc.gov/hpdonline/"
HPD_TOKEN_URL = "https://mspwvw-hpdleov3.nyc.gov/authenticationservice/1.0/api/Apim/token"
HPD_HISTORIC_IMAGES_URL = "https://mspwvw-hpdleov3.nyc.gov/hpdonline.api/1.0/api/building/historicimage/list"
HPD_DOCUMENT_URL = "https://mspwvw-hpdleov3.nyc.gov/DocService/v1/api/documents/content"


LAND_USE = {
    "01": "One & Two Family Buildings",
    "02": "Multi-Family Walk-Up Buildings",
    "03": "Multi-Family Elevator Buildings",
    "04": "Mixed Residential & Commercial Buildings",
    "05": "Commercial & Office Buildings",
    "06": "Industrial & Manufacturing",
    "07": "Transportation & Utility",
    "08": "Public Facilities & Institutions",
    "09": "Open Space & Outdoor Recreation",
    "10": "Parking Facilities",
    "11": "Vacant Land",
}


@dataclass
class PropertyRecord:
    input_address: str
    matched_address: str = ""
    status: str = "OK"
    error: str = ""
    bbl: str = ""
    bin: str = ""
    landmark_status: str = "No"
    families: str = ""
    violations: str = ""
    co: str = "No"
    co_date: str = "No"
    co_download_status: str = "Not attempted"
    co_retry_url: str = ""
    historical_image_cards: str = "No"
    historical_image_cards_date: str = "No"
    land_use: str = ""
    lot_area: str = ""
    lot_frontage: str = ""
    lot_depth: str = ""
    year_built: str = ""
    year_altered: str = ""
    building_class: str = ""
    zoning_districts: str = ""
    hpd_url: str = HPD_ONLINE_URL
    dob_url: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        value = vars(self).copy()
        value["notes"] = "; ".join(self.notes)
        return value


class NYCPropertyClient:
    def __init__(self, session: requests.Session | None = None, timeout: int = 25):
        self.session = session or self._retrying_session()
        self.session.headers.update({"User-Agent": "getAddressInfo/1.0 (local property research tool)"})
        self.timeout = timeout

    @staticmethod
    def _retrying_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            other=0,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _json(self, url: str, params: dict[str, Any]) -> Any:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def lookup(self, address: str) -> tuple[PropertyRecord, list[tuple[str, bytes]]]:
        record = PropertyRecord(input_address=address)
        files: list[tuple[str, bytes]] = []
        try:
            feature = self._geocode(address)
            props = feature["properties"]
            pad = props.get("addendum", {}).get("pad", {})
            bbl = str(pad.get("bbl") or props.get("bbl") or "")
            if not re.fullmatch(r"\d{10}", bbl):
                raise ValueError("GeoSearch 没有返回有效的 10 位 BBL")
            record.bbl = bbl
            # MapPLUTO 26v1 can omit BIN; GeoSearch PAD still supplies it.
            record.bin = str(pad.get("bin") or props.get("bin") or "").strip()
            record.matched_address = props.get("label") or address
            boro, block, lot = bbl[0], str(int(bbl[1:6])), str(int(bbl[6:10]))
            data = self._pluto(boro, block, lot)
            self._apply_pluto(record, data)
            self._add_bin_from_footprints(record)
            record.dob_url = (
                f"https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?"
                f"bin={record.bin}&go4=+GO+&requestid=0" if record.bin else DOB_NOW_PUBLIC_URL
            )
            self._add_hpd(record, boro, block, lot, files)
            self._add_co(record, files)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            record.status = "ERROR"
            record.error = str(exc)
        return record, files

    def _geocode(self, address: str) -> dict[str, Any]:
        pluto_error: Exception | None = None
        try:
            return self._geocode_with_pluto(address)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            pluto_error = exc

        try:
            data = self._json(GEOSEARCH_URL, {"text": address})
            features = data.get("features") or []
            if features:
                return features[0]
            raise ValueError("GeoSearch could not resolve the address")
        except (requests.RequestException, ValueError, KeyError, TypeError) as geosearch_error:
            raise ValueError(
                f"MapPLUTO exact match failed ({pluto_error}); GeoSearch failed ({geosearch_error})"
            ) from geosearch_error

    def _geocode_with_pluto(self, address: str) -> dict[str, Any]:
        street_address, zipcode = self._address_and_zip(address)
        escaped_address = street_address.replace("'", "''")
        where = f"upper(address)='{escaped_address}'"
        if zipcode:
            where += f" AND zipcode='{zipcode}'"
        rows = self._json(PLUTO_URL, {"$where": where, "$limit": 10})
        if not rows:
            raise ValueError("MapPLUTO could not find an exact address match")
        row = rows[0]
        bbl = str(row.get("bbl") or "").split(".", 1)[0].zfill(10)
        if not re.fullmatch(r"\d{10}", bbl):
            boro = str(row.get("borocode") or "")
            block = str(row.get("block") or "").zfill(5)
            lot = str(row.get("lot") or "").zfill(4)
            bbl = f"{boro}{block}{lot}"
        if not re.fullmatch(r"\d{10}", bbl):
            raise ValueError("MapPLUTO did not return a valid 10-digit BBL")
        return {
            "properties": {
                "label": row.get("address") or street_address,
                "bbl": bbl,
                "bin": row.get("bin") or "",
                "addendum": {"pad": {"bbl": bbl, "bin": row.get("bin") or ""}},
            }
        }

    @staticmethod
    def _address_and_zip(address: str) -> tuple[str, str]:
        street_address = address.split(",", 1)[0].strip().upper()
        street_address = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", street_address)
        suffixes = {
            "ST": "STREET", "AVE": "AVENUE", "AV": "AVENUE",
            "RD": "ROAD", "BLVD": "BOULEVARD", "DR": "DRIVE",
            "PL": "PLACE", "CT": "COURT", "PKWY": "PARKWAY",
            "TER": "TERRACE", "LN": "LANE", "HWY": "HIGHWAY",
        }
        words = street_address.split()
        if words and words[-1] in suffixes:
            words[-1] = suffixes[words[-1]]
        zipcode_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
        return " ".join(words), zipcode_match.group(1) if zipcode_match else ""

    def _pluto(self, boro: str, block: str, lot: str) -> dict[str, Any]:
        rows = self._json(
            PLUTO_URL,
            {"$where": f"borocode={int(boro)} AND block={int(block)} AND lot={int(lot)}", "$limit": 5},
        )
        if not rows:
            raise ValueError("PLUTO 中找不到该地块")
        return rows[0]

    @staticmethod
    def _apply_pluto(record: PropertyRecord, data: dict[str, Any]) -> None:
        record.matched_address = data.get("address") or record.matched_address
        record.bin = str(data.get("bin") or record.bin or "")
        landmark = str(data.get("landmark") or "").strip()
        historic_district = str(data.get("histdist") or "").strip()
        landmark_name = landmark or historic_district
        record.landmark_status = f"L - LANDMARK ({landmark_name})" if landmark_name else "No"
        record.families = str(data.get("unitsres") or "")
        landuse = str(data.get("landuse") or "").zfill(2)
        record.land_use = LAND_USE.get(landuse, landuse)
        record.lot_area = str(data.get("lotarea") or "")
        record.lot_frontage = str(data.get("lotfront") or "")
        record.lot_depth = str(data.get("lotdepth") or "")
        record.year_built = str(data.get("yearbuilt") or "")
        altered = str(data.get("yearalter1") or "")
        record.year_altered = "" if altered in {"", "0"} else altered
        record.building_class = format_building_class(str(data.get("bldgclass") or ""))
        record.zoning_districts = ", ".join(
            str(data.get(key)) for key in ("zonedist1", "zonedist2", "zonedist3", "zonedist4") if data.get(key)
        )

    def _add_bin_from_footprints(self, record: PropertyRecord) -> None:
        if record.bin or not record.bbl:
            return
        try:
            rows = self._json(
                BUILDING_FOOTPRINTS_URL,
                {"$where": f"base_bbl='{record.bbl}'", "$limit": 10},
            )
            record.bin = next(
                (str(row.get("bin") or "").strip() for row in rows if row.get("bin")),
                "",
            )
        except (requests.RequestException, ValueError, KeyError, TypeError):
            record.notes.append("NYC Building Footprints BIN lookup failed")
    def _add_hpd(
        self,
        record: PropertyRecord,
        boro: str,
        block: str,
        lot: str,
        files: list[tuple[str, bytes]],
    ) -> None:
        where = f"boroid={int(boro)} AND block={int(block)} AND lot={int(lot)}"
        try:
            buildings = self._json(HPD_BUILDINGS_URL, {"$where": where, "$limit": 5})
            if not record.bin and isinstance(buildings, list):
                record.bin = next(
                    (str(building.get("bin") or "").strip() for building in buildings if building.get("bin")),
                    "",
                )
                if record.bin:
                    record.dob_url = (
                        "https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?"
                        f"bin={record.bin}&go4=+GO+&requestid=0"
                    )
            violations = self._json(
                HPD_VIOLATIONS_URL,
                {"$select": "count(*) as total", "$where": where, "$limit": 1},
            )
            record.violations = str((violations[0] if violations else {}).get("total") or "0")

            # HPD Online now lists cards by building ID and returns the PDF
            # through its public document service. The old /PDFs/Icard_*.pdf
            # route serves the Angular app instead of a PDF.
            for building in buildings if isinstance(buildings, list) else []:
                building_id = str(building.get("buildingid") or "").strip()
                if not building_id:
                    continue
                downloaded = self._download_hpd_image_card(building_id)
                if downloaded:
                    _source_name, content, card_date = downloaded
                    files.append((self._i_card_output_filename(record), content))
                    record.historical_image_cards = "Yes"
                    record.historical_image_cards_date = card_date or "No"
                    return

            # Retain the legacy route as a fallback for older/mirrored records.
            candidates = []
            for building in buildings if isinstance(buildings, list) else []:
                registration_id = str(building.get("registrationid") or "").strip()
                if registration_id:
                    candidates.append((f"Icard_{registration_id}.pdf", self._hpd_record_date(building)))
            for source_filename, _ in sorted(
                candidates,
                key=lambda candidate: self._file_selection_key(candidate[0], candidate[1]),
                reverse=True,
            ):
                for host in ("https://hpdonline.nyc.gov", "https://hpdonline.hpdnyc.org"):
                    content = self._download_pdf(f"{host}/HPDonline/PDFs/{source_filename}")
                    if content:
                        files.append((self._i_card_output_filename(record), content))
                        record.historical_image_cards = "Yes"
                        record.historical_image_cards_date = "No"
                        return
        except (requests.RequestException, ValueError, KeyError, TypeError):
            record.notes.append("HPD/I-Card 查询失败；请使用 HPD 链接人工复核")

    def _download_hpd_image_card(self, building_id: str) -> tuple[str, bytes, str] | None:
        token_response = self.session.post(HPD_TOKEN_URL, json={}, timeout=self.timeout)
        token_response.raise_for_status()
        token = str(token_response.json().get("token") or "")
        if not token:
            return None
        headers = {"ApiKey": f"Bearer {token}"}
        listing = self.session.get(
            f"{HPD_HISTORIC_IMAGES_URL}/{building_id}",
            headers=headers,
            timeout=self.timeout,
        )
        listing.raise_for_status()
        candidates = (listing.json().get("responseData") or [])
        candidates = sorted(
            candidates,
            key=lambda item: self._file_selection_key(
                f"Icard_{item.get('imageSeqNo', '')}.{item.get('fileType') or 'pdf'}",
                str(item.get("dateTaken") or ""),
            ),
            reverse=True,
        )
        for item in candidates:
            identifiers = (
                item.get("imageSeqNo"),
                item.get("docId"),
                item.get("docTypeId"),
                item.get("subDocTypeId"),
            )
            if not all(value is not None for value in identifiers):
                continue
            response = self.session.get(
                f"{HPD_DOCUMENT_URL}/{'/'.join(str(value) for value in identifiers)}",
                headers=headers,
                timeout=max(self.timeout, 60),
            )
            response.raise_for_status()
            payload = response.json().get("responseData") or {}
            encoded = payload.get("documentBytes")
            if not encoded:
                continue
            content = base64.b64decode(encoded)
            if content.startswith(b"%PDF"):
                extension = str(item.get("fileType") or "pdf")
                filename = f"Icard_{item.get('imageSeqNo')}.{extension}"
                card_date = str(item.get("dateTaken") or "").split(" ")[0]
                return filename, content, card_date
        return None

    def download_co(self, address: str, bin_number: str) -> tuple[PropertyRecord, list[tuple[str, bytes]]]:
        record = PropertyRecord(input_address=address, matched_address=address, bin=bin_number)
        files: list[tuple[str, bytes]] = []
        self._add_co(record, files)
        return record, files

    def _add_co(self, record: PropertyRecord, files: list[tuple[str, bytes]]) -> None:
        if not record.bin:
            record.co_download_status = "Skipped: no BIN"
            return
        record.co_retry_url = f"{LEGACY_CO_URL}?requestid=2&allbin={record.bin}"
        dates: list[str] = []
        legacy_error = ""
        try:
            response = self.session.get(
                LEGACY_CO_URL,
                params={"requestid": 2, "allbin": record.bin},
                timeout=self.timeout,
            )
            response.raise_for_status()
            if b"Access Denied" in response.content[:2000]:
                raise requests.HTTPError("BIS access denied", response=response)
            soup = BeautifulSoup(response.content, "html.parser")
            seen_filenames: set[str] = set()
            for anchor in soup.select("a[href]"):
                href = str(anchor.get("href") or "")
                match = re.search(r"([A-Za-z0-9_-]+\.PDF)", href + " " + anchor.get_text(" ", strip=True), re.I)
                if not match or "cofo" not in href.casefold():
                    continue
                filename = match.group(1)
                content = self._download_pdf(urljoin(response.url, href))
                if content and filename.casefold() not in seen_filenames:
                    files.append((filename, content))
                    seen_filenames.add(filename.casefold())

            forms = soup.select("form[action*='CofoJobDocumentServlet']")
            for index, form in enumerate(forms, start=1):
                form_data = {
                    node.get("name"): node.get("value", "")
                    for node in form.select("input[name]")
                    if node.get("name")
                }
                label = form.get("id", "")
                match = re.search(r"([A-Za-z0-9_-]+\.PDF)", str(form), re.I)
                filename = match.group(1) if match else f"CO_{record.bin}_{index}.pdf"
                content = None
                try:
                    posted = self.session.post(
                        LEGACY_CO_POST_URL,
                        data=form_data,
                        headers={"Referer": response.url},
                        timeout=self.timeout,
                    )
                    posted.raise_for_status()
                    content = posted.content if posted.content.startswith(b"%PDF") else None
                    if not content:
                        page = BeautifulSoup(posted.content, "html.parser")
                        viewer = page.select_one("iframe[src], embed[src], object[data]")
                        if viewer:
                            viewer_url = viewer.get("src") or viewer.get("data")
                            content = self._download_pdf(urljoin(LEGACY_CO_POST_URL, viewer_url))
                except requests.RequestException:
                    # BIS occasionally rejects or times out on the wrapper POST even
                    # though its document-content servlet remains available.
                    pass
                if not content:
                    content_params = {
                        key: value
                        for key, value in form_data.items()
                        if key == "passjobnumber" or key.startswith("cofomatadata")
                    }
                    content = self._download_pdf_with_params(LEGACY_CO_CONTENT_URL, content_params)
                if content and filename.casefold() not in seen_filenames:
                    files.append((filename, content))
                    seen_filenames.add(filename.casefold())
                dates.extend(self._extract_dates(label + " " + form.get_text(" ", strip=True)))
        except requests.RequestException as exc:
            legacy_error = str(exc)
            record.notes.append(f"旧版 BIS CO 文件查询失败：{exc}")

        try:
            rows = self._json(DOB_NOW_CO_DATA_URL, {"$q": record.bin, "$limit": 200})
            for row in rows if isinstance(rows, list) else []:
                row_text = " ".join(str(value) for value in row.values())
                if record.bin in row_text:
                    dates.extend(self._extract_dates(row_text))
                    record.notes.append("DOB NOW 中有 CO 记录；官方未提供稳定的批量 PDF 直链，请从 DOB NOW 链接打印")
                    break
        except requests.RequestException:
            record.notes.append("DOB NOW CO 数据查询失败")

        co_files = [name for name, _ in files if name.lower().startswith("co_") or name.lower().endswith(".pdf") and "i-card" not in name.lower()]
        if co_files or dates:
            record.co = "Yes"
            if dates:
                record.co_date = sorted(set(dates), key=self._date_sort_key)[-1]

        if co_files:
            record.co_download_status = "Downloaded"
        elif legacy_error:
            record.co_download_status = f"Failed: {legacy_error}"
        elif dates:
            record.co_download_status = "CO record found; manual download required"
        else:
            record.co_download_status = "No downloadable CO found"

    def _download_pdf(self, url: str) -> bytes | None:
        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if response.content.startswith(b"%PDF") or "application/pdf" in content_type:
            return response.content
        return None

    def _download_pdf_with_params(self, url: str, params: dict[str, str]) -> bytes | None:
        response = self.session.get(url, params=params, timeout=self.timeout, allow_redirects=True)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if response.content.startswith(b"%PDF") or "application/pdf" in content_type:
            return response.content
        return None

    @staticmethod
    def _extract_dates(text: str) -> list[str]:
        return re.findall(r"\b(?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])[-/](?:19|20)\d{2}\b", text)

    @staticmethod
    def _date_sort_key(value: str) -> datetime:
        for fmt in ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        return datetime.min

    @classmethod
    def _file_selection_key(cls, filename: str, date: str) -> tuple[datetime, int]:
        """Order HPD files by date, then by the largest numeric filename."""
        numbers = re.findall(r"\d+", Path(filename).stem)
        numeric_filename = int(numbers[-1]) if numbers else -1
        return cls._date_sort_key(date), numeric_filename

    @staticmethod
    def _hpd_record_date(building: dict[str, Any]) -> str:
        """Return the most useful date exposed by an HPD building record."""
        for key in ("laststatusdate", "statusdate", "registrationenddate", "registrationdate"):
            if building.get(key):
                return str(building[key])
        return ""

    @staticmethod
    def _i_card_output_filename(record: PropertyRecord) -> str:
        """Build a readable, filesystem-safe filename from the matched address."""
        address = record.matched_address or record.input_address or "property"
        stem = re.sub(r"[^A-Za-z0-9]+", "_", address).strip("_")
        return f"{stem or 'property'}_i-card.pdf"
