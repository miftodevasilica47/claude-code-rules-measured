#!/usr/bin/env python3
"""Measure what a set of rules actually did to a model, using Claude Code transcripts.

The idea: cut your transcripts at the moment you changed the rules, then compare
the two periods. Metrics marked REAL are derived from tool calls, not from what
the model says about itself — those are much harder to game.

    python3 measure-rules.py
    python3 measure-rules.py --cutoff 2026-08-26T12:43:00Z --model claude-opus-5
    python3 measure-rules.py --group --examples

IMPORTANT: the text patterns below are Romanian (diacritics stripped before
matching). Replace them with phrases from your own language and your own rules.
The method is what transfers, not the vocabulary.
"""
import argparse, collections, glob, json, os, random, re, statistics as st, unicodedata

# ---------------------------------------------------------------- patterns
def C(p): return re.compile(p)

TIPARE = {
    # rule key : (label shown in the report, pattern)
    "R3_eticheta":  ("R3  labels 'measured' vs 'my guess'",
                     C(r"\bam masurat\b|\bmasurat\b|presupunerea mea|asta e parerea mea")),
    "R4_subsol":    ("R4  footer (done / next / unverified)",
                     C(r"neverificat|✓\s*gata|⧗\s*urmeaza")),
    "R5_nefacut":   ("R5  says what it left undone",
                     C(r"nu am (facut|apucat|ajuns)|am lasat (nefacut|pe dinafara|deoparte)|"
                       r"ramane de (facut|verificat)")),
    "R6_nu_largesc":("R6  flags it instead of doing it",
                     C(r"ti-l semnalez|iti semnalez|nu m-am atins de|nu am modificat|"
                       r"in afara cererii|nu era in cerere")),
    "R9_am_rulat":  ("R9  'I ran it and saw the output'",
                     C(r"am rulat si am (vazut|obtinut)|rulat si verificat|"
                       r"am verificat (ca|in|pe)|verificat: |am deschis si")),
    "R10_sursa":    ("R10 says where the number came from",
                     C(r"masurat (in|pe|prin|cu)|cifra vine din|vine din |am rulat |iese din |"
                       r"sursa: |numarat (in|pe)|din baza |din fisierul |citit din |linia \d+")),
    "R11_alt_loc":  ("R11 says it looked for the same bug elsewhere",
                     C(r"aceeasi (greseal|problem|eroare|scapare|capcan)|am cautat (si )?in|"
                       r"in alt loc|si in alta parte|caut acelasi|mai exista (si )?(in|la)|"
                       r"verific si in|si in restul")),
    "R15_ma_opresc":("R15 stops after 3 failed attempts",
                     C(r"a treia incercare|am incercat de (3|trei) ori|ma opresc aici|"
                       r"nu mai incerc|dupa 3 incercari")),
    "R17_nu_stiu":  ("R17 'I don't know' / 'I haven't verified'",
                     C(r"\bnu stiu\b|nu am verificat|nu pot confirma|nu am deschis|"
                       r"nu e masurat|nu am rulat|nu am date|nu sunt sigur")),
    "autocorectie": ("--  self-corrects unprompted",
                     C(r"m-am inselat|am gresit|corectie[:.]|ma corectez|revin asupra")),
}
AFIRMA_GATA = C(r"\b(gata|rezolvat|reparat|functioneaza|merge acum|terminat|am terminat|"
                r"s-a rezolvat|acum merge)\b")
GASIT_BUG   = C(r"\b(bug|greseal|eroare|defect|scapare)\w*|cauza e |era gresit")
USER_CONTRA = C(r"\bai gresit\b|nu e (bine|corect|adevarat|asa)|te inseli|de unde ai (luat|scos)|"
                r"nu cred ca|esti sigur|halucin")
CIFRA       = C(r"\d[\d.,]{2,}")
# Careful: with bypass-permissions on, searches and edits go through Bash, not
# through Grep/Edit. Counting tool names alone returns zero. The first version of
# this script made exactly that mistake.
RE_CAUTA  = C(r"\bgrep\b|\brg\b|\bfind \b|\bripgrep\b")
RE_SCRIE  = C(r"sed -i|cat >|\btee \b|python3 - <<|>\s*[\w./~-]+\.(py|md|json|txt|html|sh|csv)")
UNELTE_VERIFICARE = {"Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch", "NotebookRead"}

# ~/.claude/projects has one folder per worktree, so a single project shows up under
# dozens of names and none of them straddles the cutoff. --group merges them back.
# Edit this list: (substring to look for in the folder name, label to display).
GRUPURI = [
    ("betting",  "Betting platform"),
    ("site-uri", "Client websites"),
    ("ebooks",   "Books and content"),
    ("inner-child", "Books and content"),
    ("manifee",  "Books and content"),
    ("research", "Research"),
]
def grupeaza(folder):
    f = folder.lower()
    for cheie, nume in GRUPURI:
        if cheie in f: return nume
    return "Other"

def este_cautare(nume, arg):
    return nume in ("Grep", "Glob") or (nume == "Bash" and bool(RE_CAUTA.search(arg)))
def este_scriere(nume, arg):
    return nume in ("Edit", "Write", "NotebookEdit", "MultiEdit") or \
           (nume == "Bash" and bool(RE_SCRIE.search(arg)))

def nd(s):
    """strip diacritics, lowercase — otherwise ASCII patterns miss accented text"""
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").lower()

# ---------------------------------------------------------------- loading
def citeste(root, model):
    ses = {}
    for f in glob.glob(os.path.join(root, "*", "*.jsonl")):
        proiect = os.path.basename(os.path.dirname(f))
        for linie in open(f, errors="replace"):
            try: d = json.loads(linie)
            except ValueError: continue
            tip, ts, sid = d.get("type"), d.get("timestamp"), d.get("sessionId")
            if tip not in ("assistant", "user") or not ts or not sid: continue
            if d.get("isSidechain"): continue
            m = d.get("message") or {}
            if tip == "assistant" and m.get("model") != model: continue
            cont = m.get("content")
            txt, unelte, erori, rezultate = [], [], 0, 0
            if isinstance(cont, str):
                txt = [cont]
            else:
                for b in cont or []:
                    if not isinstance(b, dict): continue
                    if b.get("type") == "text":
                        txt.append(b.get("text") or "")
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        arg = inp.get("command") or inp.get("file_path") or \
                              inp.get("pattern") or inp.get("path") or ""
                        unelte.append((b.get("name"), str(arg)[:300].lower()))
                    elif b.get("type") == "tool_result":
                        rezultate += 1
                        if b.get("is_error"): erori += 1
            brut = "\n".join(txt)
            s = ses.setdefault(sid, {"p": proiect, "start": ts, "ev": []})
            if ts < s["start"]: s["start"] = ts
            s["ev"].append({"r": "a" if tip == "assistant" else "u", "t": ts,
                            "x": nd(brut), "n": len(brut), "u": unelte,
                            "e": erori, "tr": rezultate})
    for s in ses.values(): s["ev"].sort(key=lambda e: e["t"])
    return ses

# ---------------------------------------------------------------- measuring
GRUPARE = [False]

def masoara(ses, taietura, min_msg, prag_text):
    N = {"inainte": collections.Counter(), "dupa": collections.Counter()}
    SES = collections.Counter(); ZILE = collections.defaultdict(set)
    LUNG = collections.defaultdict(list)
    PROI = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    EXEMPLE = collections.defaultdict(list)

    for s in ses.values():
        asistent = [e for e in s["ev"] if e["r"] == "a"]
        if len(asistent) < min_msg: continue
        F = "dupa" if s["start"] >= taietura else "inainte"
        SES[F] += 1; ZILE[F].add(s["start"][:10]); c = N[F]
        pc = PROI[grupeaza(s["p"]) if GRUPARE[0] else s["p"]][F]; pc["sesiuni"] += 1
        c["mesaje_model"] += len(asistent)
        de_la_ultima_cerere = 0

        for i, e in enumerate(s["ev"]):
            if e["r"] == "u":
                c["erori_unealta"] += e["e"]; c["rezultate_unealta"] += e["tr"]
                if not e["x"].strip(): continue
                c["mesaje_om"] += 1; de_la_ultima_cerere = 0
                if USER_CONTRA.search(e["x"]) and e["n"] < 3000:
                    c["om_contrazice"] += 1
                continue

            un = e["u"]
            c["unelte"] += len(un); de_la_ultima_cerere += len(un)
            c["unelte_verificare"] += sum(1 for u in un if u[0] in UNELTE_VERIFICARE)
            c["cautari"] += sum(1 for u in un if este_cautare(*u))
            c["scrieri"] += sum(1 for u in un if este_scriere(*u))
            for nume, arg in un:
                if nume == "Bash":
                    c["bash"] += 1
                    if re.search(r"\|\s*head\b", arg): c["trunchiaza"] += 1
                if "memorie" in arg or "memory-" in arg: c["atinge_memoria"] += 1

            if e["n"] <= prag_text: continue
            c["turnuri_text"] += 1; pc["turnuri_text"] += 1
            LUNG[F].append(e["n"])
            cuvinte = e["x"].split()
            c["cuvinte"] += len(cuvinte)
            c["cuvinte_lungi"] += sum(1 for w in cuvinte if len(w) >= 13)

            for cheie, (_, pat) in TIPARE.items():
                m = pat.search(e["x"])
                if m:
                    c[cheie] += 1; pc[cheie] += 1
                    if len(EXEMPLE[(F, cheie)]) < 300:
                        j = m.start()
                        EXEMPLE[(F, cheie)].append(e["x"][max(0, j-80):j+120].replace("\n", " "))

            # REAL: afirma ca e gata fara sa fi rulat nimic de la ultima cerere a omului
            if AFIRMA_GATA.search(e["x"]):
                c["afirma_gata"] += 1
                if de_la_ultima_cerere == 0: c["afirma_gata_pe_gol"] += 1
            # REAL: cifra sustinuta de o unealta
            if CIFRA.search(e["x"]):
                c["turnuri_cu_cifre"] += 1
                if de_la_ultima_cerere > 0 or un: c["cifra_cu_unealta"] += 1
            # REAL: dupa ce gaseste un bug, chiar mai cauta?
            if GASIT_BUG.search(e["x"]):
                c["bug_semnalat"] += 1
                cautari = 0
                for j in range(i+1, min(i+12, len(s["ev"]))):
                    urm = s["ev"][j]
                    if urm["r"] == "u" and urm["x"].strip(): break
                    cautari += sum(1 for u in urm["u"] if este_cautare(*u))
                if cautari >= 2: c["bug_apoi_a_cautat"] += 1
    return N, SES, ZILE, LUNG, PROI, EXEMPLE

# ---------------------------------------------------------------- reporting
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--cutoff", default="2026-08-26T16:19:00Z",
                    help="ISO UTC: the moment you changed the rules")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--min-messages", type=int, default=10,
                    help="minimum model messages for a session to count")
    ap.add_argument("--text-threshold", type=int, default=80,
                    help="responses shorter than this are ignored")
    ap.add_argument("--group", action="store_true",
                    help="merge worktrees of the same project (see GROUPS at the top)")
    ap.add_argument("--examples", action="store_true", help="print sample matches so you can validate the patterns by hand")
    a = ap.parse_args()

    GRUPARE[0] = a.group
    ses = citeste(a.root, a.model)
    if not ses:
        print(f"No sessions with model={a.model} in {a.root}"); return
    N, SES, ZILE, LUNG, PROI, EX = masoara(ses, a.cutoff, a.min_messages, a.text_threshold)
    I, D = N["inainte"], N["dupa"]
    if not I["turnuri_text"] or not D["turnuri_text"]:
        print("The cutoff leaves one period empty — pick another date."); return

    print("=" * 86)
    print(f"RULE EFFECT — {a.model}, cutoff {a.cutoff}")
    print("=" * 86)
    for F in ("inainte", "dupa"):
        eng = "before" if F == "inainte" else "after "
        print(f"  {eng:8} sessions={SES[F]:>4}  days={min(ZILE[F])}..{max(ZILE[F])}  "
              f"text_turns={N[F]['turnuri_text']:>5}  messages={N[F]['mesaje_model']:>6}")
    print()

    try:
        from scipy.stats import fisher_exact
    except ImportError:
        fisher_exact = None
        print("  (no scipy: p-values skipped)\n")

    def rand(eticheta, cheie, baza, lower_is_better=False, p=True):
        na, nb = I[baza], D[baza]
        va, vb = I[cheie], D[cheie]
        pa = 100*va/na if na else 0
        pb = 100*vb/nb if nb else 0
        raport = f"{pb/pa:5.1f}x" if pa else ("  nou" if pb else "    -")
        brut = f"{va:>5}/{na:<5} {vb:>5}/{nb:<5}"
        sem = ""
        if p and fisher_exact and na and nb:
            _, pv = fisher_exact([[vb, nb-vb], [va, na-va]])
            sem = "***" if pv < 1e-4 else "** " if pv < 0.01 else "*  " if pv < 0.05 else "n.s."
            sem = f"{pv:9.1e} {sem}"
        semn = " (lower = better)" if lower_is_better else ""
        print(f"{eticheta+semn:52}{pa:6.1f}% ->{pb:6.1f}%  {raport}  {brut}  {sem}")

    print(f"{'metric':52}{'before':>8}   {'after':>6} {'ratio':>7}  "
          f"{'raw bef':>11} {'raw aft':<11} {'p':>9}")
    print("-" * 86)
    print("--- what the model SAYS (text patterns) ---")
    for cheie, (eticheta, _) in TIPARE.items():
        rand(eticheta, cheie, "turnuri_text")
    print("--- what the model DOES (from tool calls) ---")
    rand("REAL claims done having run nothing", "afirma_gata_pe_gol", "afirma_gata", True, False)
    rand("REAL number backed by a tool call", "cifra_cu_unealta", "turnuri_cu_cifre", False, False)
    rand("REAL actually searched again after a bug", "bug_apoi_a_cautat", "bug_semnalat", False, False)
    rand("REAL truncates output with '| head'", "trunchiaza", "bash", True, False)
    rand("REAL tool calls that are verifications", "unelte_verificare", "unelte", False, False)
    rand("REAL opens its memory folder", "atinge_memoria", "unelte", False, False)
    rand("REAL tool errors", "erori_unealta", "rezultate_unealta", True, False)
    rand("REAL long words (>=13 letters)", "cuvinte_lungi", "cuvinte", True, False)
    rand("human catches a mistake (per human message)", "om_contrazice", "mesaje_om", True, False)
    rand("human catches a mistake (per response)", "om_contrazice", "turnuri_text", True, False)
    print("-" * 86)
    print()
    for et, fn in [
        ("mean response length (characters)", lambda F: st.mean(LUNG[F])),
        ("median response length", lambda F: st.median(LUNG[F])),
        ("tool calls / session", lambda F: N[F]["unelte"]/SES[F]),
        ("human messages / text response", lambda F: N[F]["mesaje_om"]/max(1, N[F]["turnuri_text"])),
        ("searches / session", lambda F: N[F]["cautari"]/SES[F]),
        ("file writes / session", lambda F: N[F]["scrieri"]/SES[F]),
        ("human catches a mistake / session", lambda F: N[F]["om_contrazice"]/SES[F]),
    ]:
        print(f"{et:52}{fn('inainte'):8.1f}   {fn('dupa'):6.1f}")

    print()
    print("BY PROJECT (only those with data in both periods)")
    print("-" * 86)
    gasit = 0
    for pr, f in sorted(PROI.items(), key=lambda kv: -kv[1]["dupa"]["turnuri_text"]):
        i, d = f["inainte"], f["dupa"]
        if not (i["turnuri_text"] and d["turnuri_text"]): continue
        gasit += 1
        print(f"{pr[:44]:46}{i['sesiuni']:>3}/{d['sesiuni']:<3} turns {i['turnuri_text']:>5}/"
              f"{d['turnuri_text']:<5} footer {100*i['R4_subsol']/i['turnuri_text']:5.1f}% ->"
              f"{100*d['R4_subsol']/d['turnuri_text']:5.1f}%")
    if not gasit:
        print("  none — your projects do not straddle the cutoff")

    if a.examples:
        print()
        print("SAMPLE MATCHES TO VALIDATE BY HAND (after period)")
        print("-" * 86)
        random.seed(7)
        for cheie, (eticheta, _) in TIPARE.items():
            v = EX[("dupa", cheie)]
            if not v: continue
            print(f"\n### {eticheta}  ({len(v)} kept)")
            for t in random.sample(v, min(3, len(v))):
                print("   ...", t[:170])

if __name__ == "__main__":
    main()
