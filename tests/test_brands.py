from tests.conftest import make_product


def body(p, **over):
    keys = ("caliber_id", "brand", "name", "bullet_weight_gr", "bullet_type", "rounds_per_box")
    return {**{k: p[k] for k in keys}, **over}


def test_brand_list_is_distinct_and_most_used_first(authed):
    make_product(authed, brand="Federal", name="A")
    make_product(authed, brand="Federal", name="B")
    make_product(authed, brand="Hornady", name="C")
    make_product(authed, brand="", name="D")
    assert authed.get("/api/brands").json() == ["Federal", "Hornady"]


def test_typed_brand_takes_the_spelling_already_on_file(authed):
    make_product(authed, brand="Federal", name="A")
    p = make_product(authed, brand="  FEDERAL ", name="B")
    assert p["brand"] == "Federal"
    q = make_product(authed, brand="Sellier & Bellot", name="C")
    r = authed.put(f"/api/products/{q['id']}", json=body(q, brand="federal")).json()
    assert r["brand"] == "Federal"
    assert authed.get("/api/brands").json() == ["Federal"]


def test_a_lone_brand_can_still_be_recased(authed):
    p = make_product(authed, brand="Pmc", name="A")
    r = authed.put(f"/api/products/{p['id']}", json=body(p, brand="PMC")).json()
    assert r["brand"] == "PMC"
    assert authed.get("/api/brands").json() == ["PMC"]


def test_import_reuses_existing_spelling_and_agrees_with_itself(authed):
    make_product(authed, brand="Federal", name="A", rounds_per_box=50)
    csv = ("caliber,manufacturer,name,rounds_per_box\n"
           "9mm Luger,federal,X,50\n"
           "9mm Luger,Winchester,Y,50\n"
           "9mm Luger,WINCHESTER,Z,100\n")
    r = authed.post("/api/import/products?apply=true", content=csv, headers={"content-type": "text/csv"})
    assert r.status_code == 200, r.text
    assert sorted(authed.get("/api/brands").json()) == ["Federal", "Winchester"]
    names = {p["name"]: p["brand"] for p in authed.get("/api/products").json()}
    assert names["X"] == "Federal" and names["Y"] == "Winchester" and names["Z"] == "Winchester"
