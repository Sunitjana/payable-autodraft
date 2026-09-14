from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class MasterValidationResult:
    valid: bool
    supplier_valid: bool = True
    po_valid: bool = True
    taxes_valid: bool = True
    payment_terms_valid: bool = True
    account_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class MasterValidator:
    """Validate populated master-data IDs/codes without rejecting honest blanks.

    The challenge explicitly states that an unmatched supplier/tax/PO/term is a
    legitimate empty value. Only a *populated* ERP identifier is required to
    exist in its corresponding master.
    """

    def __init__(self, suppliers=None, po_master=None, tax_master=None, payment_terms=None, chart_of_books=None):
        self.suppliers = suppliers or []
        self.po_master = po_master or []
        self.tax_master = tax_master or []
        self.payment_terms = payment_terms or []
        self.chart_of_books = chart_of_books or []

    @staticmethod
    def _norm(v: Any) -> str:
        return "".join(ch for ch in str(v or "").casefold() if ch.isalnum())

    @staticmethod
    def _records(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            out=[]
            for v in data.values():
                out.extend(MasterValidator._records(v))
            return out
        return []

    @staticmethod
    def _first(rec: dict[str, Any], *keys: str) -> Any:
        for k in keys:
            if rec.get(k) not in (None, ""):
                return rec[k]
        return None

    def _exists(self, value: Any, data: Any, *keys: str) -> bool:
        if value in (None, ""):
            return True
        needle=self._norm(value)
        return any(self._norm(self._first(r,*keys)) == needle for r in self._records(data))

    def validate(self, payload: Dict[str, Any]) -> MasterValidationResult:
        errors=[]; warnings=[]
        supplier=payload.get("supplier") or {}
        buyer=payload.get("buyer") or {}

        sid=self._first(supplier,"supplier_id","supplier_code","vendor_id") if isinstance(supplier,dict) else None
        if sid and not self._exists(sid,self.suppliers,"supplier_id","supplier_code","vendor_id","id","code"):
            errors.append("supplier_id is populated but does not exist in supplier master.")
        elif not sid:
            warnings.append("supplier_id is unresolved/blank; this is permitted by the challenge.")

        pid=payload.get("po_id")
        if pid and not self._exists(pid,self.po_master,"po_id","purchase_order_id","id","code"):
            errors.append("po_id is populated but does not exist in PO master.")
        elif not pid and payload.get("po_number"):
            warnings.append("PO number is present but no ERP PO match was established.")

        term=payload.get("payment_term_id")
        if term and not self._exists(term,self.payment_terms,"payment_term_id","payment_terms_id","term_id","id","code"):
            errors.append("payment_term_id is populated but does not exist in payment-term master.")
        elif not term:
            warnings.append("payment_term_id is unresolved/blank.")

        tax_valid=True
        for i,t in enumerate(payload.get("taxes") or [],1):
            code=t.get("tax_type_code") if isinstance(t,dict) else None
            if code and not self._exists(code,self.tax_master,"tax_type_code","tax_code","code","id"):
                tax_valid=False; errors.append(f"taxes[{i}].tax_type_code is not in tax master.")
            elif not code:
                warnings.append(f"taxes[{i}].tax_type_code is unresolved/blank.")

        account_valid=True
        if isinstance(buyer,dict):
            for key, data in (("company_code",self.chart_of_books),("business_unit_code",self.chart_of_books),("location_code",self.chart_of_books)):
                val=buyer.get(key)
                if val and not self._exists(val,data,key):
                    account_valid=False; errors.append(f"buyer.{key} is not in chart_of_books.")
                elif not val:
                    warnings.append(f"buyer.{key} is unresolved/blank.")

        return MasterValidationResult(
            valid=not errors,
            supplier_valid=not any("supplier_id" in e for e in errors),
            po_valid=not any("po_id" in e for e in errors),
            taxes_valid=tax_valid,
            payment_terms_valid=not any("payment_term_id" in e for e in errors),
            account_valid=account_valid,
            errors=errors,
            warnings=warnings,
        )
