"""SEC EDGAR filings.

EDGAR is public data the SEC publishes for exactly this purpose, but access is
conditional: they require a descriptive User-Agent carrying a contact address
and cap traffic at 10 requests/second. Both are enforced here — set
MKT_CONTACT_EMAIL before pointing this at sec.gov.
"""
from __future__ import annotations

from ..models import Filing
from ..providers.base import Provider, SymbolNotFound, parse_date


class EdgarScraper(Provider):
    name = "edgar"
    WWW = "https://www.sec.gov"
    DATA = "https://data.sec.gov"

    _ticker_map: dict[str, str] | None = None

    def _headers(self) -> dict[str, str]:
        contact = self.settings.contact_email
        agent = self.settings.user_agent
        if contact and contact not in agent:
            agent = f"{agent} ({contact})"
        return {"User-Agent": agent, "Accept": "application/json"}

    async def _cik_for(self, symbol: str) -> str:
        """Resolve ticker to a zero-padded 10-digit CIK."""
        if EdgarScraper._ticker_map is None:
            base = self.settings.base_url(self.name, self.WWW)
            payload = await self.http.get_json(
                f"{base}/files/company_tickers.json",
                headers=self._headers(), ttl=86400.0, check_robots=True,
            )
            mapping: dict[str, str] = {}
            rows = payload.values() if isinstance(payload, dict) else payload
            for row in rows:
                ticker = str(row.get("ticker", "")).upper()
                cik = row.get("cik_str") or row.get("cik")
                if ticker and cik is not None:
                    mapping[ticker] = str(cik).zfill(10)
            EdgarScraper._ticker_map = mapping
        cik = EdgarScraper._ticker_map.get(symbol.upper())
        if not cik:
            raise SymbolNotFound(f"edgar: no CIK for {symbol}")
        return cik

    async def filings(
        self, symbol: str, forms: tuple[str, ...] = (), limit: int = 20
    ) -> list[Filing]:
        cik = await self._cik_for(symbol)
        base = self.settings.base_url(f"{self.name}_data", self.DATA)
        payload = await self.http.get_json(
            f"{base}/submissions/CIK{cik}.json",
            headers=self._headers(), ttl=900.0, check_robots=True,
        )
        recent = ((payload or {}).get("filings") or {}).get("recent") or {}
        form_list = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []

        wanted = {f.upper() for f in forms}
        out: list[Filing] = []
        for i, form in enumerate(form_list):
            if wanted and form.upper() not in wanted:
                continue
            accession = accessions[i] if i < len(accessions) else ""
            document = documents[i] if i < len(documents) else ""
            stripped = accession.replace("-", "")
            url = (
                f"{self.WWW}/Archives/edgar/data/{int(cik)}/{stripped}/{document}"
                if stripped and document
                else f"{self.WWW}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            )
            out.append(Filing(
                symbol=symbol, form=form,
                filed=parse_date(dates[i] if i < len(dates) else ""),
                title=(descriptions[i] if i < len(descriptions) else "") or form,
                url=url, accession=accession,
            ))
            if len(out) >= limit:
                break
        return out
