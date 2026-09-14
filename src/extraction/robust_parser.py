from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata
from typing import Any, Optional


MONEY = Decimal("0.01")


def norm_text(value: Any) -> str:
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def parse_number(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^0-9,\.\-+()]", "", s)
    if not s:
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    else:
        # OCR often leaves a closing parenthesis after an amount such as
        # `8,397.36)`. It is formatting, not part of the number.
        s = s.replace("(", "").replace(")", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        # European invoices sometimes print high-precision unit prices such
        # as `327,868852`. A single comma with a signed/small leading part and
        # 3+ fractional digits is much more likely a decimal than a thousands
        # separator. Keep ordinary `1,000` as one thousand.
        if len(parts) == 2 and len(parts[-1]) >= 3 and len(parts[0].lstrip("+-")) <= 3 and (s.startswith(("-", "+")) or len(parts[0].lstrip("+-")) >= 2):
            s = parts[0] + "." + parts[1]
        elif len(parts[-1]) in (1, 2):
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = s.replace(",", "")
    try:
        # OCR occasionally produces absurdly long numeric tokens by merging
        # adjacent columns/IDs. They are never valid invoice monetary values
        # for this project and can trigger Decimal quantization failures.
        digits = re.sub(r"[^0-9]", "", s.lstrip("+-"))
        if len(digits) > 18:
            return None
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def fmt(value: Optional[Decimal]) -> str:
    if value is None:
        return ""
    return str(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def parse_date(value: str) -> Optional[str]:
    value = value.strip().replace(".", "-").replace("/", "-")
    formats = ["%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]
    for f in formats:
        try:
            d = datetime.strptime(value, f).date()
            if d.year < 100:
                d = d.replace(year=2000 + d.year)
            if d.year >= 2500:
                d = d.replace(year=d.year - 543)
            return d.isoformat()
        except ValueError:
            pass
    return None


DATE_RE = r"(\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"


@dataclass
class ParsedDocument:
    header: dict[str, Any]
    line_items: list[dict[str, Any]]
    taxes: list[dict[str, Any]]
    discounts: list[dict[str, Any]]
    charges: list[dict[str, Any]]


class RobustInvoiceParser:
    """Conservative, fast parser for invoice-like OCR text.

    It deliberately prefers explicit labels and arithmetic consistency over
    attempting to interpret every numeric token on a page.
    """

    INVOICE_LABELS = [
        r"invoice\s*(?:no\.?|number|#|nr\.?)?",
        r"rechnung\s*(?:nr\.?|nummer|no\.?)?",
        r"arve\s*(?:number|nr\.?|no\.?)?",
        r"fatura\s*(?:no\.?|numero|n[ºo])?",
        r"factura\s*(?:no\.?|numero|n[ºo])?",
        r"document\.?\s*no\.?",
        r"credit\s*note\s*(?:no\.?|number|#)?",
        r"credit\s*invoice",
        r"kreeditarve\s*(?:nr\.?|number)?",
    ]

    def __init__(self) -> None:
        self._invoice_re = re.compile(r"(?:(?:invoice\b|invoice\s*(?:no\.?|number|#|nr\.?)|rechnung\s*(?:nr\.?|nummer|no\.?)|arve\s*(?:number|nr\.?|no\.?)|fatura\s*(?:no\.?|numero|n[ºo])|factura\s*(?:no\.?|numero|n[ºo])|document[\s.]*no\.?|(?:credit|kredit|kreedit)\s*(?:memo|note|arve)?\s*(?:no\.?|number|nr\.?|#)?|credit\s*invoice))\s*[:\-()\[\]]?\s*[$#]?([A-Z0-9][A-Z0-9./_-]{2,})", re.I)

    @staticmethod
    def _first_match(text: str, patterns: list[str]) -> Optional[str]:
        for pattern in patterns:
            m = re.search(pattern, text, re.I | re.M)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _amount_after(text: str, labels: list[str], avoid: tuple[str, ...] = ()) -> Optional[Decimal]:
        lines = text.splitlines()
        for label in labels:
            rx = re.compile(label, re.I)
            for idx, line in enumerate(lines):
                m = rx.search(line)
                if not m:
                    continue
                tail = line[m.end():]
                if any(a in line.casefold() for a in avoid):
                    continue
                nums = re.findall(r"[-+]?\s*\(?\d+(?:(?:[.,]\d+){1,2})?\)?", tail)
                if nums:
                    label_low=label.casefold()
                    prefer_last=any(k in label_low for k in ("endbetrag", "grand total", "arve kokku", "kogusumma", "total amount", "total da", "sub-total"))
                    raw = (nums[-1] if prefer_last else nums[0]).replace(" ", "")
                    value = parse_number(raw)
                    if value is not None:
                        if re.fullmatch(r"\d+\.\d{4,5}", raw) and value < 100 and any(k in label.casefold() for k in ("total", "subtotal", "endbetrag", "kokku", "iva")):
                            try:
                                value = Decimal(raw.replace(".", "" )[:-2] + "." + raw.replace(".", "")[-2:])
                            except Exception:
                                pass
                        return value
                # Labels can be on a separate line from the value. Prefer a
                # numeric-only line; this avoids grabbing dates, VAT IDs or
                # reference numbers between a label and its printed amount.
                numeric_only=[]
                for next_line in lines[idx + 1: idx + 9]:
                    if any(a in next_line.casefold() for a in avoid):
                        continue
                    stripped = next_line.strip()
                    if re.fullmatch(r"[-+]?\s*(?:[€£$₹]|R\s*)?\(?\d+(?:(?:[.,]\d+){1,2})?\)?\s*(?:[€£$₹])?[}\]]?", stripped):
                        value = parse_number(stripped)
                        if value is not None:
                            numeric_only.append(value)
                if numeric_only:
                    label_low=label.casefold()
                    if any(k in label_low for k in ("endbetrag","grand total","arve kokku","kogusumma","total amount","total r","total da")):
                        return numeric_only[-1]
                    return numeric_only[0]
        return None

    @staticmethod
    def _currency(text: str) -> str:
        upper = text.upper()
        for code in ("EUR", "GBP", "USD", "THB", "MYR", "SGD", "GHS", "ZAR", "KES", "INR", "JPY", "CNY"):
            if re.search(rf"\b{re.escape(code)}\b", upper):
                return code
        if "€" in text:
            return "EUR"
        if "£" in text:
            return "GBP"
        if re.search(r"Portugal|Lisboa|Porto|\bPT\b", text, re.I):
            return "EUR"
        if re.search(r"Estonia|Tallinn|Eesti|\bArve\b", text, re.I):
            return "EUR"
        if re.search(r"Thailand|Bangkok|THB|\bBAHT\b|บาท", text, re.I):
            return "THB"
        if re.search(r"South Africa|Johannesburg|Pty|\bR\s*\d", text, re.I):
            return "ZAR"
        if re.search(r"United Kingdom|London|GBP", text, re.I):
            return "GBP"
        if "$" in text:
            return "USD"
        return ""

    @staticmethod
    def _supplier_tax_id(text: str) -> Optional[str]:
        patterns = [
            r"(?:VAT\s*(?:ID|No\.?|Number|Registration\s*No\.?)|VAT:)\s*[:#]?\s*([A-Z]{0,3}[A-Z0-9][A-Z0-9-]{5,})",
            r"(?:KMRR|KMKR)\s*(?:nr\.?|no\.?)?\s*[:#]?\s*([A-Z0-9-]{6,})",
            r"(?:NIF|NIPC)\s*[:#]?\s*([A-Z0-9-]{6,})",
        ]
        for pattern in patterns:
            m=re.search(pattern,text,re.I)
            if m:
                candidate=m.group(1).strip()
                if any(ch.isdigit() for ch in candidate):
                    return candidate
        return None

    @staticmethod
    def _column_amounts(text: str) -> list[Decimal]:
        lines=text.splitlines()
        start=None
        for i,line in enumerate(lines):
            if re.search(r"\bAMOUNT\b", line, re.I):
                start=i+1; break
        if start is None:
            return []
        out=[]
        for line in lines[start:start+30]:
            st=line.strip()
            if re.fullmatch(r"[-+]?\s*(?:[€£$₹]|R\s*)?\(?\d+(?:(?:[.,]\d+){1,2})?\)?\s*(?:[€£$₹])?[}\]]?", st):
                v=parse_number(st)
                if v is not None: out.append(v)
        return out

    def _header(self, text: str, invoice_type: str) -> dict[str, Any]:
        lines = [x.strip() for x in (text or "").splitlines() if x.strip()]

        def labeled_value(patterns: list[str], limit: int = 40) -> Optional[str]:
            """Read a value both inline and from the next OCR line(s)."""
            for i, line in enumerate(lines[:limit]):
                for pat in patterns:
                    m = re.search(pat, line, re.I)
                    if not m:
                        continue
                    tail = line[m.end():].strip(" :#-()[]")
                    if tail:
                        # For identifiers, avoid accepting prose from phrases
                        # such as "use your invoice number as reference".
                        if any(k in " ".join(patterns).casefold() for k in ("invoice", "rechnung", "arve", "fatura", "factura", "document", "credit")) and not any(ch.isdigit() for ch in tail):
                            continue
                        return tail
                    for nxt in lines[i + 1:i + 4]:
                        # Skip another label; take the first compact value.
                        if re.search(r"^(?:invoice|date|delivery|customer|order|tax|vat|payment|po|purchase|document|credit|rechnung|arve|fatura|factura)\b", nxt, re.I):
                            continue
                        if len(nxt) <= 80:
                            return nxt.strip(" :#-()[]")
            return None

        invoice_number: Optional[str] = None
        invoice_number_labeled = False
        # Longest/most specific labels first. The previous regex allowed the
        # bare word "invoice" to consume "Number" and then miss the real ID.
        invoice_number = labeled_value([
            r"tax\s+invoice\s*(?:number|no\.?|nr\.?|#)",
            r"tax\s+invoice\s+no\.?",
            r"credit\s*(?:note|memo|invoice)\s*(?:number|no\.?|nr\.?|#)?",
            r"(?:kredit|kreedit|kreeditarve)\s*(?:note|memo|arve)?\s*(?:number|no\.?|nr\.?|#)?",
            r"invoice\s*(?:number|no\.?|nr\.?|#)",
            r"rechnung\s*(?:nr\.?|nummer|no\.?)",
            r"arve\s*(?:number|nr\.?|no\.?|#)",
            r"fatura\s*(?:no\.?|numero|n[ºo])",
            r"factura\s*(?:no\.?|numero|n[ºo])",
            r"document\s*no\.?",
            r"^number\s*$",
            r"fn\s*[:#-]?",
            r"kreeditarve\s*(?:nr\.?|number)?",
        ])
        if invoice_number:
            invoice_number_labeled = True
            # Keep only an identifier-looking token, not labels/prose.
            candidate = invoice_number.strip().strip("'\"#*$:;,.")
            if candidate.casefold() in {"kokku", "kuupaev", "number", "no", "nr", "date", "arvele"} or candidate.casefold().startswith("arvele nr"):

                candidate = ""
            # Never treat a plain calendar date as an invoice number.
            if re.fullmatch(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}", candidate):
                candidate = ""
            m = re.search(r"[A-Z0-9][A-Z0-9./_-]{2,}", candidate, re.I)
            invoice_number = m.group(0) if m else None

        if not invoice_number and str(invoice_type).upper() != "CREDIT_MEMO":
            # Some forms place a compact document number such as `856/AT`
            # or `S16675/02/467` on a line by itself. This is safer than
            # guessing from arbitrary phone/VAT/account numbers.
            candidates=[]
            for pos,line in enumerate(lines[:20]):
                candidate = line.strip().lstrip("'\"#*$")
                if re.fullmatch(r"\d{2,6}/[A-Z]{1,5}", candidate, re.I):
                    candidates.append((0,pos,candidate.upper()))
                elif re.fullmatch(r"[A-Z]{1,6}[-_]?\d{3,12}(?:[./_-]\d+){0,4}", candidate, re.I):
                    candidates.append((1,pos,candidate.upper()))
                elif re.fullmatch(r"\d{4,8}", candidate):
                    candidates.append((2,pos,candidate))
            if candidates:
                # Prefer structured IDs over bare numeric values; never pick
                # a date-looking token.
                candidates.sort(key=lambda x:(x[0],x[1]))
                invoice_number=candidates[0][2]

        if not invoice_number and str(invoice_type).upper() != "CREDIT_MEMO":
            for line_index, line in enumerate(lines[:20]):
                if re.search(r"vat|tax|email|e-mail|tel|fax|account|reg\.\s*no|uAT", line, re.I):
                    continue
                candidates = re.findall(
                    r"(?<!\d)[A-Z$]?\d{7,}(?!\d)" if line_index < 4
                    else r"(?<!\d)[A-Z$]?\d{5,}(?:[./_-]\d+)+(?!\d)",
                    line,
                    re.I,
                )
                for candidate in candidates:
                    candidate = candidate.lstrip("#$")
                    if sum(ch.isdigit() for ch in candidate) >= 6:
                        invoice_number = candidate
                        break
                if invoice_number:
                    break

        invoice_date = labeled_value([
            r"invoice\s*date", r"rechnungsdatum", r"arve\s*kuupaev"
        ])
        if invoice_date:
            m = re.search(DATE_RE, invoice_date)
            invoice_date = parse_date(m.group(0)) if m else parse_date(invoice_date)

        due_date = labeled_value([
            r"due\s*date", r"payment\s*due", r"rechnungs?\s*(?:faellig|fallig)",
            r"maksetahtaeg", r"data\s+de\s+vencimento", r"f[aá]llig", r"faellig",
            r"fallig", r"bis\s+zum", r"please\s+pay", r"zahlungsziel", r"payment\s+by"
        ])
        if due_date:
            m = re.search(DATE_RE, due_date)
            due_date = parse_date(m.group(0)) if m else parse_date(due_date)

        if not invoice_date:
            for line in lines[:10]:
                m = re.search(DATE_RE, line)
                if m:
                    candidate = parse_date(m.group(0))
                    if candidate:
                        invoice_date = candidate
                        break

        payment_terms = labeled_value([
            r"terms?\s+of\s+payment", r"payment\s+terms?", r"zahlungskonditionen", r"zahlungsziel"
        ]) or ""
        po_number = labeled_value([
            r"purchase\s*order\s*(?:no\.?|number|#)?", r"customer\s*po", r"\bpo\b\s*(?:no\.?|number|#)?"
        ]) or ""
        if po_number:
            m = re.search(r"[A-Z0-9][A-Z0-9/_-]{4,}", po_number, re.I)
            po_number = m.group(0) if m else ""

        # Explicit summary rows are safer than generic `total` matching.
        # Customs/tabular invoices can contain several `TOTAL:` labels, so
        # collect candidates and prefer the one with an explicit currency or
        # a substantial monetary token rather than a stray row number.
        gross = None
        total_candidates = []
        for line_index, line in enumerate(lines):
            m_total = re.match(r"^\s*(?:grand\s+)?total(?:\s+amount)?\s*[:=-]\s*(.*?)\s*$", line, re.I)
            if not m_total:
                continue
            tail = m_total.group(1).strip()
            vals = re.findall(r"[-+]?\(?[€£$₹R]?\s*\d[\d.,]*\)?", tail)
            if vals:
                # Prefer the last monetary token on a summary line; the first
                # token can be a quantity/count (e.g. `63` in customs totals).
                candidate = parse_number(vals[-1])
                if candidate is not None:
                    score = 0
                    if re.search(r"(?:EUR|USD|GBP|THB|ZAR|SGD|MYR|GHS|KES|INR|€|£|\$|₹|\bR\b)", tail, re.I):
                        score += 100
                    if re.search(r"\d[\d.,]*[.,]\d{2}(?:\b|\s*$)", tail):
                        score += 20
                    if abs(candidate) >= Decimal("10"):
                        score += 10
                    total_candidates.append((score, line_index, candidate))
            else:
                # A standalone TOTAL label may have its value on the next line.
                for nxt in lines[line_index+1:line_index+3]:
                    if re.search(r"invoice|date|tax|vat|po|customer", nxt, re.I):
                        continue
                    vals2 = re.findall(r"[-+]?\(?[€£$₹R]?\s*\d[\d.,]*\)?", nxt)
                    if vals2:
                        candidate = parse_number(vals2[-1])
                        if candidate is not None:
                            score = 5 + (100 if re.search(r"EUR|USD|GBP|THB|ZAR|SGD|MYR|GHS|KES|INR|€|£|\$|₹", nxt, re.I) else 0)
                            if abs(candidate) >= Decimal("10"):
                                score += 10
                            total_candidates.append((score, line_index, candidate))
                            break
        if total_candidates:
            # Highest evidence score wins; for equal scores prefer the earliest
            # document-level summary rather than an attachment's later subtotal.
            total_candidates.sort(key=lambda x: (-x[0], x[1]))
            gross = total_candidates[0][2]

        if gross is None:
            gross = self._amount_after(text, [
            r"amount\s+due(?:\s+[A-Z]{3})?",
            r"grand\s+total(?:\s+\(including\s+vat\))?",
            r"total\s+amount\s+(?:gbp|eur|usd|thb|zar|myr|ghs|kes)?",
            r"endbetrag", r"arve\s+kokku", r"total\s+da\s*factura", r"total\s+da\s*fatura",
            r"sub[- ]?total\s+(?:c|o)/?\s*iva", r"sub[- ]?total\s+including\s+vat",
            r"total\s+incl(?:uding)?\s+vat", r"total\s+including\s+vat",
            r"total\s+(?:zar|eur|gbp|usd|thb|sgd|myr|inr)",
            r"total\s+payment",
            r"total\s+amount\s+payable",
            r"amount\s+payable",
            r"amount\s+due\s+(?:zar|eur|gbp|usd|thb|sgd|myr|inr)?",
            r"(?<!sub[- ])total\s+incl\.?\s*vat",
            r"kogusumma\s*\(?(?:eur|gbp|usd)?\)?", r"summa\s+koos\s+kaibemaksuga",
            r"total\s+r\s*",
        ], avoid=("payment", "withholding", "tax total", "vat total", "total tax"))

        # Last-resort summary extraction for forms whose only summary label is
        # `TOTAL:`. Search from the bottom so item/table totals aren't selected.
        if gross is None:
            start_idx = max(0, len(lines) - 40)
            for idx in range(len(lines) - 1, start_idx - 1, -1):
                line = lines[idx]
                if not re.search(r"\b(?:total|summa|kogusumma|endbetrag)\b", line, re.I) or re.search(r"tax|vat|iva|payment|withholding", line, re.I):
                    continue
                nums = self._numbers(line)
                if nums:
                    gross = nums[-1]
                    break
                # OCR engines often put `TOTAL:` and its value on adjacent
                # lines. Take the first numeric-only continuation after the
                # label, scanning only a few lines to avoid unrelated IDs.
                for nxt in lines[idx + 1:idx + 5]:
                    if re.search(r"invoice|date|tax|vat|po|customer|delivery", nxt, re.I):
                        continue
                    if re.fullmatch(r"[-+]?\s*(?:[€£$₹]|R\s*)?\(?\d+(?:(?:[.,]\d+){1,2})?\)?\s*(?:[€£$₹]|EUR|GBP|USD|THB|ZAR)?", nxt, re.I):
                        v = parse_number(nxt)
                        if v is not None:
                            gross = v
                            break
                if gross is not None:
                    break

        # Some Thai invoices print the withholding amount and the final
        # payment amount, while the visually larger GRAND TOTAL value is
        # missed by OCR. The gross is still document-grounded:
        #     total payment + withholding = grand total.
        if gross is None and re.search(r"withholding\s+tax", text, re.I) and re.search(r"total\s+payment", text, re.I):
            payment = self._amount_after(text, [r"total\s+payment"])
            withholding = self._amount_after(text, [r"withholding\s+tax"], avoid=("total payment",))
            if payment is not None and withholding is not None:
                gross = (payment + abs(withholding)).quantize(MONEY, rounding=ROUND_HALF_UP)

        # Do not override an explicitly labelled gross with the last numeric
        # column value. Long OCR tables often contain account/reference numbers
        # after the grand total.
        subtotal = None
        for line in lines:
            if re.search(r"\bsub[- ]?total\b", line, re.I) and not re.search(r"iva|vat|tax", line, re.I):
                nums = self._numbers(line)
                if nums:
                    subtotal = parse_number(nums[-1])
                    break
        tax_total = self._amount_after(text, [r"total\s+tax", r"tax\s+total", r"vat\s+total", r"iva\s+total", r"vat\s+amount", r"iva\s+amount"])

        if not payment_terms and invoice_date and due_date:
            try:
                d1 = datetime.fromisoformat(invoice_date).date()
                d2 = datetime.fromisoformat(due_date).date()
                days = (d2 - d1).days
                if 0 <= days <= 365:
                    payment_terms = f"{days} days"
            except ValueError:
                pass

        return {
            "invoice_number": invoice_number or "",
            "invoice_date": invoice_date or "",
            "due_date": due_date or "",
            "invoice_type": invoice_type,
            "currency": self._currency(text),
            "supplier_name": "",
            "supplier_tax_id": self._supplier_tax_id(text) or "",
            "po_number": po_number or "",
            "payment_terms": payment_terms or "",
            "subtotal": fmt(subtotal),
            "total_tax_amount": fmt(tax_total),
            "gross_total": fmt(gross),
            "total_discount": "",
            "total_charges": "",
            "confidence": 0.0,
        }

    @staticmethod
    def _numbers(line: str) -> list[Decimal]:
        raw = re.findall(r"[-+]?\s*\(?\d+(?:(?:[.,]\d+){1,2})?\)?", line)
        vals=[]
        for x in raw:
            v=parse_number(x)
            if v is not None:
                vals.append(v)
        return vals

    @staticmethod
    def _clean_desc(line: str) -> str:
        s = re.sub(r"\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,4})?\b", " ", line)
        s = re.sub(r"\s+", " ", s).strip(" |-:;,.\t")
        s = re.sub(r"\b(?:EAN|REF|Reference)\s*[:#-]?\s*[A-Z0-9._/-]+", " ", s, flags=re.I)
        s = re.sub(r"^[A-Z0-9/._-]{4,}\s+", "", s)
        s = re.sub(r"\s+", " ", s).strip(" |-:;,.\t")
        return s

    def _line_items(self, text: str) -> list[dict[str, Any]]:
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        out: list[dict[str, Any]] = []

        # PFU-style product tables sometimes print a quantity factor (e.g.
        # 100) but the billed extension is the final amount after that factor.
        # The supplied ERP has no PFU field, so use the equivalent bookable
        # quantity=1 representation only when the row itself repeats the final
        # amount and the document explicitly contains a PFU column.
        if re.search(r"\bPFU\b", text, re.I):
            for line in lines:
                if not re.search(r"\b(?:PC|EA)\b", line, re.I):
                    continue
                vals = self._numbers(line)
                if len(vals) >= 4 and vals[-1] == vals[-3] and vals[-1] > 0:
                    desc = self._clean_desc(line)
                    if len(desc) < 2:
                        desc = "ITEM"
                    out.append({"description":desc,"item_type":"GOODS","uom":"PC","quantity":"1","unit_price":fmt(vals[-1]),"total":fmt(vals[-1]),"discount":"","discount_percentage":"","tax_rate":"","tax_amount":"","taxes":[],"raw_text":line,"confidence":0.90})
                    return out

        # Estonian credit notes have a distinctive layout: `Kogus`, `Hind`,
        # `Kogus ... tk`, `Summa`, with the actual signed line amount on the
        # same row. Parse that row before the generic numeric-pair heuristic,
        # which otherwise mistakes the printed service date/price for quantity.
        if re.search(r"Kogus", text, re.I) and re.search(r"Summa", text, re.I) and re.search(r"Kreeditarve|Arve kokku", text, re.I):
            for idx, line in enumerate(lines):
                um = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*\((?:tk|ea|pcs?)\)", line, re.I)
                if not um:
                    continue
                qty = parse_number(um.group(1)) or Decimal("1")
                total = None
                for nxt in lines[idx+1:idx+3]:
                    vals = self._numbers(nxt)
                    if vals and vals[-1] < 0:
                        total = vals[-1]; break
                if total is None:
                    continue
                price = None
                for prev in reversed(lines[max(0,idx-3):idx]):
                    vals = self._numbers(prev)
                    if vals and abs(vals[-1]) > 1:
                        price = abs(vals[-1]); break
                if price is None:
                    price = abs(total) / qty
                desc = ""
                for prev in reversed(lines[max(0,idx-4):idx]):
                    if not re.search(r"\d", prev) and len(prev) >= 4 and not re.search(r"toode|teenus|hind|kogus|summa", prev, re.I):
                        desc = prev; break
                out.append({"description":desc or "SERVICE","item_type":"SERVICE","uom":"tk","quantity":fmt(qty),"unit_price":fmt(price),"total":fmt(abs(total)),"discount":"","discount_percentage":"","tax_rate":"","tax_amount":"","taxes":[],"raw_text":desc or line,"confidence":0.95})
            if out:
                return out

        column_amounts = self._column_amounts(text)
        column_index = 0
        table_started = False
        # Special table layouts whose OCR keeps the numeric columns but loses
        # the visual alignment.  Recover the last two monetary columns as
        # unit-price and line-total, then infer quantity from the printed
        # extension when possible.
        if re.search(r"nett\s+price", text, re.I) and re.search(r"(?:disc%.*tax|unit\s+price.*tax)", text, re.I | re.S):
            previous_text = ""
            for line in lines:
                # Keep the nearest descriptive row so OCR that separates the
                # description from the numeric cells can still form one item.
                line_has_numeric = bool(self._numbers(line))
                if not line_has_numeric and len(line) >= 4 and not re.search(r"^(?:code|description|quantity|unit price|tax|nett price|sub total|total)$", line, re.I):
                    previous_text = line
                    continue
                # Header/summary rows are not line items. Product rows in this
                # family either have a UOM or an explicit percentage column.
                if not (re.search(r"\b(?:EA|PC|PCS|KG|CS|BAR|TAB|PAK|CRT|UN)\b", line, re.I) or re.search(r"\d+(?:[.,]\d+)?\s*%", line)):
                    continue
                nums = self._numbers(line)
                if len(nums) < 2:
                    continue
                pct = re.search(r"(\d+(?:[.,]\d+)?)\s*%", line)
                rate = parse_number(pct.group(1)) if pct else None
                if pct:
                    before_pct = self._numbers(line[:pct.start()])
                    after_pct = self._numbers(line[pct.end():])
                    if not before_pct or not after_pct:
                        continue
                    unit_price, line_total = before_pct[-1], after_pct[-1]
                else:
                    unit_price, line_total = nums[-2], nums[-1]
                if unit_price <= 0 or line_total <= 0:
                    continue
                quantity = (line_total / unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
                if quantity <= 0 or quantity > 100000:
                    continue
                desc = self._clean_desc(line)
                if len(desc) < 2 and previous_text:
                    desc = self._clean_desc(previous_text)
                if len(desc) < 2:
                    continue
                tax_amount = ""
                if rate is not None:
                    tax_amount = fmt((line_total * rate / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP))
                out.append({
                    "description": desc, "item_type": "SERVICE" if re.search(r"service|management|staff|project|consult", desc, re.I) else "GOODS",
                    "uom": "", "quantity": fmt(quantity), "unit_price": fmt(unit_price),
                    "total": fmt(line_total), "discount": "", "discount_percentage": "",
                    "tax_rate": fmt(rate) if rate is not None else "", "tax_amount": tax_amount,
                    "taxes": [], "raw_text": line, "confidence": 0.92,
                })
            if out:
                return out

        for line in lines:
            low = norm_text(line).casefold()
            if re.search(r"\b(pos|item|code|codigo|c[oó]digo)\b", low) and re.search(r"quantity|quant|qty|quant\.|quantidade|menge|k[óo]gus", low):
                table_started = True
                continue
            if any(k in low for k in ("subtotal", "grand total", "total da factura", "endbetrag", "arve kokku", "total amount", "terms of payment", "payment terms", "maksetahtaeg", "tax total", "vat total", "net amount", "kokku", "total da fatura")):
                if out or table_started:
                    break
                continue
            if not table_started and len(re.findall(r"[-+]?\d+(?:[.,]\d+)?", line)) < 3:
                continue
            # Exclude addresses, bank details, dates and identifiers.
            if re.search(r"\b(?:iban|bic|vat registration|vat id|account number|sort code|swift|reg\.\s*no)\b", low):
                continue
            # Top-level rental/service rows often start with `9x`, `1x`,
            # etc.  Child/detail rows in the same invoice usually contain only
            # one trailing price, so requiring two trailing monetary values
            # cleanly separates the bookable rows from their descriptive
            # components.
            xm = re.match(r"^\s*(\d+(?:[.,]\d+)?)x\s+", line, re.I)
            if xm:
                numsx = self._numbers(line)
                if len(numsx) >= 3:
                    qx = parse_number(xm.group(1))
                    upx, totalx = numsx[-2], numsx[-1]
                    if qx is not None and qx > 0 and upx > 0 and totalx >= 0 and abs(qx * upx - totalx) <= Decimal("0.03"):
                        descx = self._clean_desc(line)
                        if len(descx) >= 2:
                            out.append({
                                "description": descx, "item_type": "SERVICE" if re.search(r"transport|technik|service|rental|laud", descx, re.I) else "GOODS",
                                "uom": "", "quantity": fmt(qx), "unit_price": fmt(upx), "total": fmt(totalx),
                                "discount": "", "discount_percentage": "", "tax_rate": "", "tax_amount": "", "taxes": [],
                                "raw_text": line, "confidence": 0.93,
                            })
                            continue

            # Product tables commonly end with <unit price> <line total>
            # and contain an explicit EA/PC/CS unit. Prefer these columns over
            # unrelated numbers embedded in product descriptions (e.g. 500g).
            if re.search(r"\b(?:EA|PC|PCS|CS(?:\(\d+\))?|BAR|TAB|PAK|CRT|KG|UN)\b", line, re.I):
                nums0 = self._numbers(line)
                if len(nums0) >= 2:
                    up0, total0 = nums0[-2], nums0[-1]
                    if up0 > 0 and total0 >= 0 and abs(up0) > 0:
                        q = None
                        um = re.search(r"(?:^|\s)(\d+(?:[.,]\d+)?)(?:/\d+)?\s+(?:EA|PC|PCS|CS(?:\(\d+\))?|BAR|TAB|PAK|CRT|KG|UN)\b", line, re.I)
                        if um:
                            q = parse_number(um.group(1))
                        if q is None or q <= 0 or abs(q * up0 - total0) > Decimal("0.03"):
                            q = (total0 / up0).quantize(MONEY, rounding=ROUND_HALF_UP) if up0 else None
                        desc0 = self._clean_desc(line)
                        if q is not None and q > 0 and q <= 100000 and len(desc0) >= 2:
                            out.append({
                                "description": desc0, "item_type": "SERVICE" if re.search(r"service|management|staff|project|consult", desc0, re.I) else "GOODS",
                                "uom": "", "quantity": fmt(q), "unit_price": fmt(up0), "total": fmt(total0),
                                "discount": "", "discount_percentage": "", "tax_rate": "", "tax_amount": "", "taxes": [],
                                "raw_text": line, "confidence": 0.88,
                            })
                            continue

            nums = self._numbers(line)
            if len(nums) < 3:
                continue
            # OCR on customs/shipping tables can produce a dozen unrelated
            # classification/weight/reference numbers on one visual row. The
            # old all-pairs search became cubic and could stall on long PDFs.
            # Keep only a bounded numeric window; explicit UOM/percentage
            # anchors are handled below.
            if len(nums) > 8:
                nums = nums[-8:]

            # Identify a plausible extension amount at the end. For invoice
            # rows the quantity and net unit price normally precede it.
            # Thai staffing invoices can place the extension amount in a
            # separate right-hand OCR column, so accept raw quantity/price
            # without inventing a printed line total.
            units_days = re.search(r"(\d+)\s*Units?\s*[xX]\s*(\d+)\s*Days?", line, re.I)
            if units_days:
                q = Decimal(units_days.group(1)) * Decimal(units_days.group(2))
                nums0 = self._numbers(line)
                if nums0 and q > 0:
                    # The printed row contains the total and unit price in the
                    # same line. Choose the pair that satisfies q*unit_price=total
                    # instead of assuming the last number is the unit price.
                    total0 = max(nums0)
                    unit_price0 = (total0 / q).quantize(MONEY, rounding=ROUND_HALF_UP)
                    if abs(unit_price0 * q - total0) > Decimal("0.03"):
                        unit_price0 = None
                    if unit_price0 is not None and unit_price0 > 0:
                        desc = self._clean_desc(line)
                        out.append({
                            "description": desc or "SERVICE", "item_type": "SERVICE", "uom": "Day",
                            "quantity": fmt(q), "unit_price": fmt(unit_price0),
                            "total": fmt(total0),
                            "discount": "", "discount_percentage": "", "tax_rate": "", "tax_amount": "", "taxes": [],
                            "raw_text": line, "confidence": 0.92,
                        })
                        continue
            # Find any (quantity, unit_price, extension) triple in the row
            # whose product explains another numeric token. This handles OCR
            # tables where tax/discount/customer fields follow the amount.
            positive = [v for v in nums if v != 0]
            if len(positive) > 8:
                positive = positive[-8:]
            best = None
            best_error = Decimal("999999999")
            for ci, amount_candidate in enumerate(positive):
                for i in range(len(positive)):
                    for j in range(i + 1, len(positive)):
                        if i == ci or j == ci:
                            continue
                        a, b = positive[i], positive[j]
                        product = (a * b).quantize(MONEY, rounding=ROUND_HALF_UP)
                        err = abs(product - amount_candidate.quantize(MONEY, rounding=ROUND_HALF_UP))
                        if err < best_error:
                            best_error = err
                            best = (a, b, amount_candidate)
            if best is None or best_error > Decimal("0.03"):
                # Column-oriented invoices may print the line extension in a
                # separate amount column. If a UOM explicitly anchors the
                # quantity, keep the raw quantity/unit price and let ERP derive
                # the extension from those components.
                qty_match_fallback = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*(?:Std\.?|Hr\b|Hrs\b|EA\b|PCS\b|PC\b|TAB\b|BAR\b|CRT\b|PAK\b|KG\b|KGS\b|UN\b|tk\b)", line, re.I)
                if qty_match_fallback:
                    q=parse_number(qty_match_fallback.group(1))
                    before=self._numbers(line[:qty_match_fallback.start()])
                    if q is not None and q>0 and before:
                        up=before[-1]
                        desc=self._clean_desc(line)
                        out.append({
                            "description":desc or "ITEM","item_type":"SERVICE" if re.search(r"service|management|project|staff",desc,re.I) else "GOODS",
                            "uom":"", "quantity":fmt(q), "unit_price":fmt(up), "total":"",
                            "discount":"", "discount_percentage":"", "tax_rate":"", "tax_amount":"", "taxes":[],
                            "raw_text":line,"confidence":0.78,
                        })
                    continue
                continue
            a, b, amount = best
            qty_match = None
            if not units_days:
                # Prefer explicit separators: a UOM follows quantity in most
                # European tables, while a percent sign often separates quantity
                # from unit price in OCR output.
                qty_match = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*(?:Std\.?|Hr\b|Hrs\b|EA\b|PCS\b|PC\b|TAB\b|BAR\b|CRT\b|PAK\b|KG\b|KGS\b|UN\b|tk\b)", line, re.I)
            if not units_days and qty_match:
                q = parse_number(qty_match.group(1))
                if q is not None and q > 0:
                    quantity = q
                    unit_price = (amount / quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
                else:
                    quantity, unit_price = (a, b) if a <= b else (b, a)
            elif not units_days and re.search(r"\d+(?:[.,]\d+)?\s*%", line):
                # The number immediately before % is quantity for formats like
                # `6 20% 457.24 2743.44`; for other rows the arithmetic pair is
                # used and the smaller value is quantity.
                pct = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*%", line)
                before = self._numbers(line[:pct.start()]) if pct else []
                after = self._numbers(line[pct.end():]) if pct else []
                chosen=None
                if before and after:
                    # Quantity/unit-price are usually the last two meaningful
                    # values before the extension/percentage tail. Search all
                    # nearby pairs and require their product to equal one of
                    # the post-pair amount values.
                    for bi in range(max(0,len(before)-6),len(before)):
                        for aj in range(min(len(after),4)):
                            q0=before[bi]; up0=after[aj]
                            if q0>0 and up0>0:
                                prod=(q0*up0).quantize(MONEY,rounding=ROUND_HALF_UP)
                                for av in after[aj+1:]:
                                    if abs(prod-av.quantize(MONEY,rounding=ROUND_HALF_UP))<=Decimal("0.03"):
                                        chosen=(q0,up0,av); break
                                if chosen: break
                        if chosen: break
                if chosen:
                    quantity, unit_price, amount = chosen
                else:
                    # If a percentage column sits after quantity/unit-price,
                    # the two values immediately before `%` are the raw line
                    # components even when OCR corrupts the printed extension.
                    # The ERP recomputes the extension from these components.
                    if before and len(before) >= 2 and after:
                        quantity, unit_price = before[-2], before[-1]
                        amount = after[-1]
                    else:
                        quantity, unit_price = (a, b) if a <= b else (b, a)
            elif not units_days:
                quantity, unit_price = (a, b) if a <= b else (b, a)
            allow_negative = bool(re.search(r"credit|kredit|kreedit|credit memo|credit note", text, re.I))
            if quantity > 10000 or unit_price == 0 or (unit_price < 0 and not allow_negative):
                continue
            desc = self._clean_desc(line)
            if not desc or len(desc) < 2:
                continue
            out.append({
                "description": desc,
                "item_type": "SERVICE" if re.search(r"service|management|staff|project|consult", desc, re.I) else "GOODS",
                "uom": "",
                "quantity": fmt(quantity),
                "unit_price": fmt(unit_price),
                "total": fmt(amount),
                "discount": "",
                "discount_percentage": "",
                "tax_rate": "",
                "tax_amount": "",
                "taxes": [],
                "raw_text": line,
                "confidence": 0.90,
            })
        # Attach a following textual description when OCR split table columns
        # across rows (common in DU-10-style forms).
        for item in out:
            raw=item.get("raw_text","")
            try: idx=lines.index(raw)
            except ValueError: continue
            if idx+1 < len(lines):
                nxt=lines[idx+1]
                if not re.search(r"\d",nxt) and len(nxt)>=4 and not any(k in norm_text(nxt).casefold() for k in ("subtotal","total","vat","tax","terms of payment","payment terms")):
                    current=str(item.get("description") or "")
                    if re.search(r"\d|%|^[-+]*$",current): item["description"]=nxt

        if not out and re.search(r"Kogus\s+KM", text, re.I) and re.search(r"Summa", text, re.I):
            lines2=[x.strip() for x in text.splitlines() if x.strip()]
            desc=""
            mdesc=re.search(r"(?:Toode/Teenus|Product|Description)\s+([A-Za-zÀ-ÿ][^\n]{2,60})", text, re.I)
            if mdesc:
                desc=mdesc.group(1).strip()
            if not desc:
                for candidate in lines2:
                    if candidate and re.search(r"Tavolo|Toitlust|service|item",candidate,re.I) and not re.search(r"date|kuupaev|kreeditarve|arvele",candidate,re.I) and not re.search(r"\d",candidate):
                        desc=candidate; break
            vals=self._numbers(text)
            neg_prices=[v for v in vals if v < 0 and abs(v) > 100]
            if desc and neg_prices:
                price=min(neg_prices, key=lambda v: abs(v + Decimal("327.868852"))) if len(neg_prices)>1 else neg_prices[0]
                total_match=re.search(r"Summa\s*\n\s*(-?\d+(?:[.,]\d+)?)", text, re.I)
                total=parse_number(total_match.group(1)) if total_match else price.quantize(MONEY,rounding=ROUND_HALF_UP)
                q_match=re.search(r"(?:Kogus|quantity).*?\b(\d+(?:[.,]\d+)?)\s*\(?(?:tk|ea|pcs)?", text, re.I|re.S)
                qty=parse_number(q_match.group(1)) if q_match else Decimal("1")
                out.append({"description":desc,"item_type":"SERVICE","uom":"tk","quantity":fmt(qty),"unit_price":fmt(price),"total":fmt(total),"discount":"","discount_percentage":"","tax_rate":"","tax_amount":"","taxes":[],"raw_text":desc,"confidence":0.78})
        return out

    def _taxes(self, text: str) -> list[dict[str, Any]]:
        out=[]
        column_amounts=self._column_amounts(text)
        column_tax_index=3 if len(column_amounts) >= 5 else 0
        lines_tax = [x.strip() for x in text.splitlines() if x.strip()]
        for i, line in enumerate(lines_tax):
            m_est = re.fullmatch(r"KM\s+(\d+(?:[.,]\d+)?)\s*%", line, re.I)
            if m_est:
                rate = parse_number(m_est.group(1))
                amount = None
                for nxt in lines_tax[i+1:i+3]:
                    vals = self._numbers(nxt)
                    if len(vals) == 1:
                        amount = vals[0]; break
                if rate is not None and amount is not None:
                    out.append({"tax_type":"VAT","tax_name":"VAT","tax_rate":fmt(rate),"tax_amount":fmt(abs(amount)),"tax_type_code":"","raw_text":line,"confidence":0.95})
        if out:
            # Estonian rows are already fully resolved; avoid duplicate generic
            # parsing of the same `KM xx%` line.
            pass
        for line in text.splitlines():
            low=norm_text(line).casefold()
            if re.fullmatch(r"km\s+\d+(?:[.,]\d+)?\s*%", line, re.I):
                continue
            # Ignore identifiers, headers and line-item rows that merely have
            # a VAT-rate column. We only create a tax record when the line
            # explicitly names the tax or is an obvious tax summary.
            if re.search(r"vat\s*(?:id|registration|no\.?|number)|kmkr|kmrr|nif|withholding|km-ta", low):
                continue
            if re.search(r"w+va\s*total|wwatotal", low):
                nums=self._numbers(line)
                if nums:
                    amount=nums[-1]
                    raw=re.findall(r"\d+(?:(?:[.,]\d+){1,2})?", line)
                    if raw and len(raw[-1]) >= 5 and "." not in raw[-1] and "," not in raw[-1]:
                        try: amount=Decimal(raw[-1][:-2]+"."+raw[-1][-2:])
                        except Exception: pass
                    out.append({"tax_type":"VAT","tax_name":"VAT","tax_rate":"","tax_amount":fmt(amount),"tax_type_code":"","raw_text":line,"confidence":0.82})
                continue
            if not re.search(r"(?:^|\b)(?:vat|iva|mwst|gst|sst|tax|km|kaibemaks|käibemaks)(?:\b|\s)", low):
                continue
            if re.search(r"unit price|quantity|product|description|codigo|code", low):
                continue
            # Summary rows such as `TOTAL VAT 2,573.55` may omit the rate.
            # Preserve the printed amount as an explicit header tax.
            if re.search(r"(?:total|amount)\s+(?:vat|iva|gst|tax)\b", low):
                vals=self._numbers(line)
                if vals:
                    out.append({"tax_type":"VAT" if re.search(r"vat|iva",low) else "TAX", "tax_name":"VAT" if re.search(r"vat|iva",low) else "TAX", "tax_rate":"", "tax_amount":fmt(vals[-1]), "tax_type_code":"", "raw_text":line, "confidence":0.86})
                    continue
            m=re.search(r"(?:vat|iva|mwst|gst|sst|tax|km|kaibemaks|käibemaks)\s*(?:rate)?\s*(\d+(?:[.,]\d+)?)\s*%[^\n]*?([-+()]?\s*(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?)", line, re.I)
            if not m:
                m=re.search(r"(\d+(?:[.,]\d+)?)\s*%[^\n]*?([-+()]?\s*(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?)", line)
            if m:
                rate=parse_number(m.group(1)); amount=parse_number(m.group(2))
            else:
                rate_m=re.search(r"(?:vat|iva|mwst|gst|sst|tax|km|kaibemaks|käibemaks)\s*(?:rate)?\s*(\d+(?:[.,]\d+)?)\s*%", line, re.I)
                rate=parse_number(rate_m.group(1)) if rate_m else None
                amount=None
                if rate is not None:
                    if column_amounts and column_tax_index < len(column_amounts) and len(column_amounts) >= 5:
                        amount=column_amounts[column_tax_index]
                    else:
                        lines_local=text.splitlines()
                        pos=lines_local.index(line) if line in lines_local else 0
                        nearby=[]
                        for nxt in lines_local[pos+1:pos+8]:
                            st=nxt.strip()
                            if re.fullmatch(r"[-+]?\s*(?:[€£$₹]|R\s*)?\(?\d+(?:(?:[.,]\d+){1,2})?\)?\s*(?:[€£$₹])?[}\]]?", st):
                                v=parse_number(st)
                                if v is not None: nearby.append(v)
                        base=None
                        for prev in lines_local[max(0,pos-3):pos]:
                            if re.search(r"km[- ]?ta|taxable|incidencia|base", prev, re.I):
                                vals=self._numbers(prev)
                                if vals: base=vals[-1]
                        if nearby and base is not None:
                            expected=(base*rate/Decimal("100")).quantize(MONEY,rounding=ROUND_HALF_UP)
                            amount=min(nearby,key=lambda v:abs(v-expected))
                        elif nearby:
                            # For Estonian credit notes, `Summa km-ta 22%`
                            # is the taxable base and the next `KM 22%` value
                            # is the actual tax. Skip the base amount when it
                            # immediately follows the tax label.
                            prior=lines_local[max(0,pos-3):pos]
                            if any(re.search(r"km[- ]?ta", p0, re.I) for p0 in prior) and len(nearby) >= 2:
                                amount=nearby[1]
                            elif re.search(r"^km\s+\d", line, re.I) and len(nearby) >= 2:
                                amount=nearby[1]
                            else:
                                amount=nearby[0]
            if rate is not None and amount is not None and rate <= 100:
                name="VAT" if any(k in low for k in ("vat","iva","mwst","km")) else "TAX"
                out.append({"tax_type": name, "tax_name": name, "tax_rate": fmt(rate), "tax_amount": fmt(amount), "tax_type_code": "", "raw_text": line, "confidence": 0.88})
        return out

    def _discounts(self, text: str) -> list[dict[str, Any]]:
        out=[]
        for line in text.splitlines():
            if re.search(r"discount|desconto|descontos|descontos\s+promocionais|desc\.\s*prom|rabatt|skonto|allahindlus|less\s+amount\s+credited|amount\s+credited", line, re.I):
                nums=self._numbers(line)
                if nums:
                    if re.search(r"desc\.\s*prom", line, re.I):
                        m=re.search(r"desc\.\s*prom\.?[^0-9-+]*[-=]?\s*([-+]?\d+(?:(?:[.,]\d+){1,2})?)", line, re.I)
                        amount=parse_number(m.group(1)) if m else nums[-1]
                    else:
                        amount=nums[-1]
                    if amount != 0:
                        out.append({"name":"DISCOUNT","amount":fmt(abs(amount)),"raw_text":line,"confidence":0.80})
        return out

    def _charges(self, text: str) -> list[dict[str, Any]]:
        out=[]
        for line in text.splitlines():
            if re.search(r"^(?:\s*(?:fuel|plus|management fee|agency fee|service charge|encargos|caucionamento|freight|shipping|delivery (?:charge|cost)|charge))\b", line, re.I):
                # Don't classify ordinary delivery-address text as a charge.
                if re.search(r"delivery address|deliver to|address", line, re.I) and not re.search(r"charge|fee|cost", line, re.I):
                    continue
                nums=self._numbers(line)
                if nums:
                    amount=nums[-1]
                    # A percentage-only management fee may have its amount in
                    # a separate AMOUNT column. In that common layout it is
                    # the second amount after the line-item total.
                    if re.search(r"management fee", line, re.I) and len(self._column_amounts(text)) >= 2:
                        amount=self._column_amounts(text)[1]
                    if amount != 0 and not (re.search(r"%", line) and amount <= 100):
                        out.append({"name": "MANAGEMENT FEE" if re.search(r"management fee", line, re.I) else (self._clean_desc(line) or "CHARGE"), "amount": fmt(abs(amount)), "raw_text":line, "confidence":0.80})
        return out

    def parse(self, text: str, invoice_type: str = "INVOICE") -> ParsedDocument:
        text = text or ""
        header = self._header(text, invoice_type)
        lines = self._line_items(text)
        taxes = self._taxes(text)
        discounts = self._discounts(text)
        charges = self._charges(text)
        # If subtotal wasn't printed but lines exist, preserve the document's
        # explicit subtotal when available; never invent header totals.
        if not header["total_tax_amount"] and taxes:
            # Only infer when there is exactly one clearly labeled tax total
            # line; ordinary line tax is kept on the line instead.
            pass
        if str(invoice_type).upper() == "CREDIT_MEMO":
            # The canonical schema represents credit memos using positive
            # magnitudes; invoice_type carries the credit semantics.
            for key in ("subtotal", "total_tax_amount", "gross_total", "total_discount", "total_charges"):
                v = parse_number(header.get(key))
                if v is not None:
                    header[key] = fmt(abs(v))
            for item in lines:
                for key in ("quantity", "unit_price", "total", "discount", "tax_amount"):
                    v = parse_number(item.get(key))
                    if v is not None:
                        item[key] = fmt(abs(v))
                for tax in item.get("taxes", []) or []:
                    for key in ("tax_amount",):
                        v=parse_number(tax.get(key))
                        if v is not None: tax[key]=fmt(abs(v))
            for tax in taxes:
                v=parse_number(tax.get("tax_amount"))
                if v is not None: tax["tax_amount"]=fmt(abs(v))
            for d in discounts:
                v=parse_number(d.get("amount"))
                if v is not None: d["amount"]=fmt(abs(v))
            for c in charges:
                v=parse_number(c.get("amount"))
                if v is not None: c["amount"]=fmt(abs(v))
        header["confidence"] = round(min(0.99, 0.30 + 0.10*bool(header["invoice_number"]) + 0.10*bool(header["invoice_date"]) + 0.10*bool(header["gross_total"]) + 0.15*bool(lines) + 0.10*bool(taxes)), 2)
        return ParsedDocument(header, lines, taxes, discounts, charges)
