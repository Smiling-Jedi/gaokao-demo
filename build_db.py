#!/usr/bin/env python3
# build_db.py — 全量院校库构建：2026官方PDF转录 + 2025全量合并 → db.js
# 数据红线：专项计划/预科/民族班不进入推荐池（普通考生报不了，混入=误导）
import re, json, csv, sys
import pdfplumber

DATA = '/Users/jediyang/ClaudeCode/Project-Makemoney/高考志愿内参/data'
OUT = '/Users/jediyang/ClaudeCode/Project-Makemoney/高考志愿内参/gaokao-demo/db.js'

EXCLUDE = re.compile(r'专项|预科|民族班|定向')
LINE = re.compile(r'^(\d{4})\s+(.+?)\s+(物理类|历史类)\s+(第\S+组(?:[(（][^)）]*[)）])?)\s+(\d+)\.\d+$')

def load_yfd(path, skip=1):
    """一分一段 → {分数: 累计人数}"""
    m = {}
    with open(path, encoding='utf-8-sig') as f:
        rd = csv.reader(f)
        for _ in range(skip):
            next(rd, None)
        for row in rd:
            if not row or len(row) < 4:
                continue
            score_s, cum = row[-3].strip(), row[-1].strip()
            mm = re.match(r'(\d+)', score_s)
            if mm and cum.isdigit():
                m[int(mm.group(1))] = int(cum)
    return m

def rank_of(yfd, score):
    s = int(score)
    if s in yfd:
        return yfd[s]
    above = [k for k in yfd if k > s]
    return yfd[min(above)] if above else max(yfd.values())

def parse_pdf(path, yfd):
    rows, dropped = [], 0
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            for ln in (pg.extract_text() or '').split('\n'):
                m = LINE.match(ln.strip())
                if not m:
                    continue
                code, name, kelei, grp, score = m.groups()
                if EXCLUDE.search(grp):
                    dropped += 1
                    continue
                rows.append({'n': name, 'g': grp, 's': int(score), 'r': rank_of(yfd, int(score))})
    return rows, dropped

def main():
    yfd26_phy = load_yfd(f'{DATA}/shanxi_2026_yifenyiduan_物理类.csv')
    yfd26_his = load_yfd(f'{DATA}/shanxi_2026_yifenyiduan_历史类.csv')
    print(f'一分一段2026: 物理{len(yfd26_phy)}行 历史{len(yfd26_his)}行')

    p26, d26p = parse_pdf(f'{DATA}/官方PDF_2026本科批投档线_物理类.pdf', yfd26_phy)
    h26, d26h = parse_pdf(f'{DATA}/官方PDF_2026本科批投档线_历史类.pdf', yfd26_his)
    print(f'2026转录: 物理{len(p26)}行(剔专项等{d26p}) 历史{len(h26)}行(剔{d26h})')

    # 2025 物理全量（已带位次）
    p25 = []
    with open(f'{DATA}/投档线_山西_2025_物理类_位次补全.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if EXCLUDE.search(r['专业组']):
                continue
            p25.append({'n': r['院校名称'], 'g': r['专业组'], 's': int(r['投档最低分']), 'r': int(r['最低位次_一分一段查表'])})
    print(f'2025物理: {len(p25)}行（剔专项后）')

    # 合并：键 = 院校+专业组；物理双年对照，历史仅2026
    def merge(rows26, rows25):
        db = {}
        for r in rows26:
            db[(r['n'], r['g'])] = {'n': r['n'], 'g': r['g'], 's26': r['s'], 'r26': r['r']}
        for r in rows25:
            k = (r['n'], r['g'])
            if k in db:
                db[k]['s25'], db[k]['r25'] = r['s'], r['r']
            else:
                db[k] = {'n': r['n'], 'g': r['g'], 's25': r['s'], 'r25': r['r']}
        out = list(db.values())
        # 主排序位次：优先2026，无则2025
        for o in out:
            o['r'] = o.get('r26') or o.get('r25')
        out.sort(key=lambda o: o['r'])
        return out

    phy = merge(p26, p25)
    his = merge(h26, [])
    both26 = sum(1 for o in phy if 'r26' in o and 'r25' in o)
    print(f'合并: 物理{len(phy)}条(双年对照{both26}条) 历史{len(his)}条')

    def pack(rows):
        # [院校, 专业组, 主位次, 2026分, 2026位次, 2025分, 2025位次]（无则0）
        return [[o['n'], o['g'], o['r'], o.get('s26', 0), o.get('r26', 0), o.get('s25', 0), o.get('r25', 0)] for o in rows]

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('// 全量院校库（build_db.py 生成，勿手改）——2026官方PDF转录+2025全量；已剔专项/预科/民族班/定向\n')
        f.write('const DB_PHY=' + json.dumps(pack(phy), ensure_ascii=False, separators=(',', ':')) + ';\n')
        f.write('const DB_HIS=' + json.dumps(pack(his), ensure_ascii=False, separators=(',', ':')) + ';\n')
    import os
    print(f'输出 {OUT}: {os.path.getsize(OUT)//1024}KB')

if __name__ == '__main__':
    main()
