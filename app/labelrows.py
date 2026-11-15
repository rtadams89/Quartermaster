from sqlalchemy.orm import Session

from .codes import normalize_code
from .models import Barcode, PackCode, Product
from .services import new_label_codes, pack_code_for, product_dict, product_label_code, spec_text


class LabelError(ValueError):
    pass


def _row(code: str, kind: str, product: Product | None, pack_qty: int | None = None) -> dict:
    d = product_dict(product) if product else None
    unit = "rounds" if product and product.rounds_per_box == 1 else "boxes"
    return {"code": code, "type": kind, "product_id": product.id if product else None,
            "caliber": d["caliber"] if d else "", "product": d["label"] if d else "",
            "details": spec_text(product).replace(" · ", ", ") if product else "",
            "total": f"{pack_qty} {unit}" if pack_qty else "", "pack_qty": pack_qty, "copies": 1}


def resolve_rows(db: Session, items: list) -> list[dict]:
    """The label for each requested item, creating the codes that do not exist yet.

    An item with a code is that existing label; with a product and pack_qty, the product's pack code;
    with a product, the product's label code; with nothing, one new blank code for each copy."""
    rows = []
    for it in items:
        if it.code:
            try:
                code = normalize_code(it.code)
            except ValueError:
                raise LabelError(f"{it.code} is not a valid code")
            pack = db.get(PackCode, code)
            bc = db.get(Barcode, code)
            if pack:
                row = _row(code, "Pack", pack.product, pack.quantity)
            elif bc:
                row = _row(code, "Product" if bc.product else "Blank", bc.product)
            else:
                raise LabelError(f"No label with the code {code}")
            rows.append({**row, "copies": it.copies})
        elif it.product_id is not None:
            product = db.get(Product, it.product_id)
            if not product:
                raise LabelError("Unknown product")
            if it.pack_qty:
                code, _ = pack_code_for(db, product.id, it.pack_qty)
                rows.append({**_row(code, "Pack", product, it.pack_qty), "copies": it.copies})
            else:
                code = product_label_code(db, product.id) or new_label_codes(db, 1, product.id)[0]
                rows.append({**_row(code, "Product", product), "copies": it.copies})
        elif it.pack_qty:
            raise LabelError("Choose a product for a pack label")
        else:
            rows += [_row(code, "Blank", None) for code in new_label_codes(db, it.copies)]
        db.flush()
    return rows
