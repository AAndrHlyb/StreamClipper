"""Бренди в словах пісні: «Dolce Gabbana» → великий напис D&G + повна назва дрібно, як у референсних едітах.
Пошук нечіткий (Whisper на співі пише «Dolche Gabana», «дольче габбана» тощо). Логотипів не малюємо — лише набір шрифтом."""
import difflib
import re

# (великий напис, повна назва, варіанти написання у тексті пісні)
BRANDS = [
    ("D&G", "DOLCE & GABBANA", ["dolce gabbana", "dolce and gabbana", "dolce gabana", "dolche gabbana", "dolce", "gabbana",
                                "дольче габбана", "дольче габана", "дольче", "габбана", "габана", "d&g", "dg"]),
    ("LV", "LOUIS VUITTON", ["louis vuitton", "louie vuitton", "vuitton", "луи виттон", "луи витон", "виттон", "витон", "lv"]),
    ("GUCCI", "", ["gucci", "гуччи", "гучи"]),
    ("PRADA", "", ["prada", "прада"]),
    ("BALENCIAGA", "", ["balenciaga", "баленсиага", "баленсияга", "баленсиаго"]),
    ("CHANEL", "", ["chanel", "шанель"]),
    ("DIOR", "", ["dior", "диор"]),
    ("VERSACE", "", ["versace", "версаче"]),
    ("FENDI", "", ["fendi", "фенди"]),
    ("YSL", "YVES SAINT LAURENT", ["yves saint laurent", "saint laurent", "ysl", "сен лоран", "ив сен лоран"]),
    ("HERMÈS", "", ["hermes", "hermès", "гермес", "эрмес"]),
    ("GIVENCHY", "", ["givenchy", "живанши"]),
    ("BURBERRY", "", ["burberry", "барберри", "бёрберри"]),
    ("MONCLER", "", ["moncler", "монклер"]),
    ("ARMANI", "", ["armani", "армани"]),
    ("BALMAIN", "", ["balmain", "балмейн", "бальман"]),
    ("MOSCHINO", "", ["moschino", "москино"]),
    ("TOM FORD", "", ["tom ford", "том форд"]),
    ("BOTTEGA", "BOTTEGA VENETA", ["bottega veneta", "bottega", "боттега"]),
    ("CELINE", "", ["celine", "céline", "селин"]),
    ("MIU MIU", "", ["miu miu", "миу миу"]),
    ("RICK OWENS", "", ["rick owens", "рик оуэнс", "рик овенс"]),
    ("CHROME HEARTS", "", ["chrome hearts", "хром хартс"]),
    ("VETEMENTS", "", ["vetements", "ветмо"]),
    ("STONE ISLAND", "", ["stone island", "стон айленд", "стоун айленд"]),
    ("OFF-WHITE", "", ["off white", "off-white", "офф вайт"]),
    ("SUPREME", "", ["supreme", "суприм"]),
    ("BAPE", "", ["bape", "бейп"]),
    ("NIKE", "", ["nike", "найк", "найки"]),
    ("ADIDAS", "", ["adidas", "адидас"]),
    ("JORDAN", "", ["jordan", "jordans", "джордан", "джорданы"]),
    ("YEEZY", "", ["yeezy", "yeezys"]),
    ("ROLEX", "", ["rolex", "ролекс", "ролексы"]),
    ("CARTIER", "", ["cartier", "картье"]),
    ("PATEK", "PATEK PHILIPPE", ["patek philippe", "patek", "патек"]),
    ("AP", "AUDEMARS PIGUET", ["audemars piguet", "audemars", "одемар"]),
    ("LAMBO", "LAMBORGHINI", ["lamborghini", "lambo", "ламборгини", "ламбо", "ламба"]),
    ("FERRARI", "", ["ferrari", "феррари"]),
    ("PORSCHE", "", ["porsche", "порше"]),
    ("BENTLEY", "", ["bentley", "бентли"]),
    ("ROLLS ROYCE", "", ["rolls royce", "rolls-royce", "роллс ройс"]),
    ("MAYBACH", "", ["maybach", "майбах"]),
    ("BENZ", "MERCEDES-BENZ", ["mercedes", "benz", "мерседес", "мерс", "бенц"]),
    ("AMG", "", ["amg"]),
    ("BMW", "", ["bmw", "бмв", "бэха"]),
]


def _norm(s):
    s = s.lower().replace("&", " and ").replace("ё", "е")
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", " ", s).strip()


_VAR = [(_norm(v), b) for b in BRANDS for v in b[2]]


def match(text, with_score=False):
    """Найкращий бренд для фрази (1–3 слова) або None."""
    t = _norm(text)
    if not t: return (None, 0.0) if with_score else None
    t2 = t.replace(" and ", " ")
    best, bs = None, 0.0
    for v, b in _VAR:
        if len(v) <= 3:  # короткі (lv, amg, bmw) — лише точний збіг
            if t == v or t2 == v: return (b, 1.0) if with_score else b
            continue
        r = max(difflib.SequenceMatcher(None, t, v).ratio(), difflib.SequenceMatcher(None, t2, v.replace(" and ", " ")).ratio())
        if r > bs: best, bs = b, r
    if bs < 0.84: best = None
    return (best, bs) if with_score else best


def find(words):
    """words: [{w, s, e}] → [(i0, i1, big, small)] — проміжки слів, що називають бренд (довші збіги першими)."""
    out, i = [], 0
    while i < len(words):
        hit, hk = None, 0.0
        for n in (1, 2, 3):  # найточніший збіг; за рівності — довший («dolce gabbana» краще за «dolce»)
            if i + n > len(words): continue
            seg = words[i:i + n]
            if n > 1 and seg[-1]["s"] - seg[0]["e"] > 1.2: continue
            b, r = match(" ".join(w["w"] for w in seg), True)
            if b and r + 0.001 * n > hk:
                hit, hk = (i, i + n, b[0], b[1]), r + 0.001 * n
        if hit:
            # «Dolce» + «Gabbana» окремими словами одного бренду — зливаємо
            if out and out[-1][2] == hit[2] and out[-1][1] == hit[0] and out[-1][1] - out[-1][0] == 1 and hit[1] - hit[0] == 1:
                out[-1] = (out[-1][0], hit[1], hit[2], hit[3])
            else:
                out.append(hit)
            i = hit[1]
        else:
            i += 1
    return out
