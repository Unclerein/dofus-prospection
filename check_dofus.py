#!/usr/bin/env python3
"""
check_dofus.py - Etape 0 : le trafic Dofus 3 est-il chiffre ?

Analyse une capture .pcapng et produit UNIQUEMENT un rapport synthetique.
Le script n'affiche JAMAIS le contenu brut des paquets (protection du jeton
de session) : seulement des statistiques et des oui/non.

Usage :
    python check_dofus.py capture.pcapng MARQUEUR [--pseudo NomPerso] [--ports 5555,443]
"""
import argparse
import math
import sys
from collections import Counter, defaultdict

try:
    from scapy.all import PcapReader, IP, IPv6, TCP
except ImportError:
    sys.exit("Scapy manquant : lance  pip install scapy")


# ---------- mesures ----------

def entropy(data: bytes) -> float:
    """Entropie de Shannon en bits/octet (8.0 = bruit parfait)."""
    if not data:
        return 0.0
    n = len(data)
    return -sum(v / n * math.log2(v / n) for v in Counter(data).values())


def printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    return sum(1 for b in data if 32 <= b < 127) / len(data)


def ascii_runs(data: bytes, minlen: int = 6) -> int:
    """Nombre de suites de >= minlen caracteres lisibles (compte seulement)."""
    count = run = 0
    for b in data:
        if 32 <= b < 127:
            run += 1
        else:
            if run >= minlen:
                count += 1
            run = 0
    return count + (1 if run >= minlen else 0)


PB_TAGS = (0x08, 0x10, 0x12, 0x18, 0x1A, 0x20, 0x22)


def protobuf_score(data: bytes) -> float:
    """Sur-representation des octets d'en-tete protobuf usuels.
    ~1.0 = aleatoire ; nettement > 2 = structure protobuf probable."""
    if len(data) < 256:
        return 0.0
    c = Counter(data)
    observed = sum(c[t] for t in PB_TAGS) / len(data)
    return observed / (len(PB_TAGS) / 256)


def tls_coverage(data: bytes, limit: int = 200_000) -> float:
    """Part du flux qui se decoupe proprement en enregistrements TLS."""
    data = data[:limit]
    i = 0
    while i + 5 <= len(data):
        t, v1, v2 = data[i], data[i + 1], data[i + 2]
        if t not in (20, 21, 22, 23) or v1 != 3 or v2 > 4:
            break
        ln = int.from_bytes(data[i + 3:i + 5], "big")
        if ln == 0 or ln > 18432:
            break
        i += 5 + ln
    return min(i, len(data)) / len(data) if data else 0.0


def extract_sni(data: bytes):
    """Nom de domaine annonce dans un ClientHello TLS (info non sensible)."""
    try:
        if data[0] != 0x16 or data[5] != 0x01:
            return None
        p = 5 + 4 + 2 + 32
        p += 1 + data[p]                                   # session id
        p += 2 + int.from_bytes(data[p:p + 2], "big")      # cipher suites
        p += 1 + data[p]                                   # compression
        end = p + 2 + int.from_bytes(data[p:p + 2], "big")
        p += 2
        while p + 4 <= end:
            et = int.from_bytes(data[p:p + 2], "big")
            el = int.from_bytes(data[p + 2:p + 4], "big")
            p += 4
            if et == 0:
                nl = int.from_bytes(data[p + 3:p + 5], "big")
                return data[p + 5:p + 5 + nl].decode("ascii", "replace")
            p += el
    except (IndexError, ValueError):
        return None
    return None


def contains(data: bytes, text: str, ignore_case: bool = False) -> bool:
    if not text:
        return False
    hay = data.lower() if ignore_case else data
    t = text.lower() if ignore_case else text
    return any(enc in hay for enc in (t.encode("utf-8"), t.encode("utf-16-le")))


# ---------- reassemblage TCP ----------

def reassemble(segments: dict) -> bytes:
    out = bytearray()
    next_seq = None
    for seq in sorted(segments):
        data = segments[seq]
        if next_seq is None or seq >= next_seq:
            out += data
            next_seq = seq + len(data)
        elif seq + len(data) > next_seq:
            out += data[next_seq - seq:]
            next_seq = seq + len(data)
    return bytes(out)


def tcp_payload(pkt, ipl, tcp) -> bytes:
    raw = bytes(tcp.payload)
    if IP in pkt:
        length = ipl.len - ipl.ihl * 4 - tcp.dataofs * 4
    else:
        length = ipl.plen - tcp.dataofs * 4
    return raw[:max(length, 0)]


# ---------- programme ----------

def main():
    ap = argparse.ArgumentParser(description="Detecte si le trafic Dofus est chiffre.")
    ap.add_argument("pcap", help="fichier .pcapng ou .pcap")
    ap.add_argument("marker", help="chaine unique envoyee dans le chat")
    ap.add_argument("--pseudo", help="nom de ton personnage (test supplementaire)")
    ap.add_argument("--ports", default="5555,443", help="ports serveur a examiner")
    args = ap.parse_args()
    ports = {int(p) for p in args.ports.split(",")}

    flows = defaultdict(lambda: {"c2s": {}, "s2c": {}, "t0": None, "t1": None})
    n_pkts = 0
    print("Lecture de la capture...", file=sys.stderr)
    with PcapReader(args.pcap) as reader:
        for pkt in reader:
            n_pkts += 1
            if TCP not in pkt:
                continue
            ipl = pkt[IP] if IP in pkt else (pkt[IPv6] if IPv6 in pkt else None)
            if ipl is None:
                continue
            tcp = pkt[TCP]
            if tcp.dport in ports:
                key, d = (ipl.dst, tcp.dport, tcp.sport), "c2s"
            elif tcp.sport in ports:
                key, d = (ipl.src, tcp.sport, tcp.dport), "s2c"
            else:
                continue
            f = flows[key]
            ts = float(pkt.time)
            f["t0"] = ts if f["t0"] is None else f["t0"]
            f["t1"] = ts
            payload = tcp_payload(pkt, ipl, tcp)
            if payload:
                f[d].setdefault(tcp.seq, payload)

    print("=" * 64)
    print("RAPPORT check_dofus  (aucun octet brut n'est affiche)")
    print("=" * 64)
    print(f"Paquets lus : {n_pkts}   |   Flux TCP sur ports {sorted(ports)} : {len(flows)}")
    if not flows:
        print("\nAucun flux trouve. Verifie dans Wireshark (Statistiques > Conversations > TCP)")
        print("le port utilise par le jeu, puis relance avec --ports <port>.")
        return

    results = []
    for key, f in flows.items():
        c2s, s2c = reassemble(f["c2s"]), reassemble(f["s2c"])
        both = c2s + s2c
        if len(both) < 200:
            continue
        tls = max(tls_coverage(c2s), tls_coverage(s2c))
        sni = extract_sni(c2s) if c2s else None
        r = {
            "server": f"{key[0]}:{key[1]}",
            "port": key[1],
            "dur": (f["t1"] or 0) - (f["t0"] or 0),
            "c2s": len(c2s), "s2c": len(s2c),
            "tls": tls, "sni": sni,
            "ent_c": entropy(c2s), "ent_s": entropy(s2c),
            "print": printable_ratio(both),
            "runs": ascii_runs(both),
            "pb": protobuf_score(s2c if len(s2c) >= len(c2s) else c2s),
            "marker": contains(both, args.marker),
            "pseudo": contains(both, args.pseudo, ignore_case=True) if args.pseudo else None,
        }
        if r["tls"] >= 0.9:
            r["verdict"] = "TLS (chiffre standard)"
        elif r["marker"] or r["pseudo"]:
            r["verdict"] = "EN CLAIR (texte connu retrouve)"
        elif max(r["ent_c"], r["ent_s"]) >= 7.8 and r["pb"] < 1.5:
            r["verdict"] = "BRUIT : chiffre maison OU compresse"
        else:
            r["verdict"] = "INDETERMINE : structure visible, texte connu absent"
        results.append(r)

    results.sort(key=lambda r: (r["port"] != 5555, -(r["c2s"] + r["s2c"])))
    for i, r in enumerate(results[:12], 1):
        print(f"\n[{i}] Serveur {r['server']}   duree {r['dur']:.0f}s")
        print(f"    Volume        : client->serveur {r['c2s']:,} o | serveur->client {r['s2c']:,} o")
        print(f"    TLS           : {'OUI' if r['tls'] >= 0.9 else 'non'} (couverture {r['tls']:.0%})"
              + (f"  SNI={r['sni']}" if r["sni"] else ""))
        print(f"    Entropie      : C->S {r['ent_c']:.2f} | S->C {r['ent_s']:.2f} bits/octet (8 = bruit)")
        print(f"    Texte lisible : {r['print']:.0%} des octets, {r['runs']} chaines >= 6 caracteres")
        print(f"    Score protobuf: {r['pb']:.1f}  (~1 = aleatoire, > 2 = structure probable)")
        print(f"    Marqueur      : {'TROUVE' if r['marker'] else 'absent'}"
              + ("" if r["pseudo"] is None else f"   |   Pseudo : {'TROUVE' if r['pseudo'] else 'absent'}"))
        print(f"    => {r['verdict']}")

    game = [r for r in results if r["port"] == 5555] or results
    print("\n" + "-" * 64)
    if any(r["marker"] or r["pseudo"] for r in game):
        print("VERDICT GLOBAL : trafic du jeu EN CLAIR -> sniffing passif viable.")
    elif game and all(r["tls"] >= 0.9 for r in game):
        print("VERDICT GLOBAL : trafic du jeu en TLS -> capture passive impossible.")
    else:
        print("VERDICT GLOBAL : non concluant -> colle ce rapport a Claude.")
    if not any(r["port"] == 5555 for r in results):
        print("Note : aucun flux sur 5555 ; les flux 443 peuvent etre du trafic web hors jeu (voir SNI).")
    print("Rappel : ne partage pas le fichier de capture lui-meme ; supprime-le apres le test.")


if __name__ == "__main__":
    main()
