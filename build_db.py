#!/usr/bin/env python3
# build_db.py v2 — v5 推荐卡落地：两年组线+计划明细+三率+就业链+tips → db.js + db_detail.js
# 数据红线：专项计划/预科/民族班/定向不进推荐池；薪酬=全国类级（麦可思2026）非校级
import re, json, csv, os, collections
import pdfplumber

DATA = '/Users/jediyang/ClaudeCode/sghdx/data'
OUT = '/Users/jediyang/ClaudeCode/sghdx/gaokao-demo/db.js'
OUT_D = '/Users/jediyang/ClaudeCode/sghdx/gaokao-demo/db_detail.js'

EXCLUDE = re.compile(r'专项|预科|民族班|定向')
LINE = re.compile(r'^(\d{4})\s+(.+?)\s+(物理类|历史类)\s+(第\S+组(?:[(（][^)）]*[)）])?)\s+(\d+)\.\d+$')
GNUM = re.compile(r'(\d+)')
PAREN = re.compile(r'[（(][^）)]*[）)]')

def load_yfd(path, skip=1):
    m = {}
    with open(path, encoding='utf-8-sig') as f:
        rd = csv.reader(f)
        for _ in range(skip):
            next(rd, None)
        for row in rd:
            if not row or len(row) < 4: continue
            mm = re.match(r'(\d+)', row[-3].strip())
            if mm and row[-1].strip().isdigit():
                m[int(mm.group(1))] = int(row[-1].strip())
    return m

def rank_of(yfd, score):
    s = int(score)
    if s in yfd: return yfd[s]
    above = [k for k in yfd if k > s]
    return yfd[min(above)] if above else max(yfd.values())

def parse_pdf(path, yfd):
    rows, dropped = [], 0
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            for ln in (pg.extract_text() or '').split('\n'):
                m = LINE.match(ln.strip())
                if not m: continue
                code, name, kelei, grp, score = m.groups()
                if EXCLUDE.search(grp): dropped += 1; continue
                g = str(int(GNUM.search(grp).group(1)))
                rows.append({'c': code, 'n': name, 'g': g, 'gt': grp, 's': int(score), 'r': rank_of(yfd, int(score))})
    return rows, dropped

def load_plans():
    """2026 招生计划 → {(校码,组码): {xk, majors[[name,cnt,fee,yr,note]]}}"""
    out = {}
    for ke in ['物理', '历史']:
        with open(f'{DATA}/prod/招生计划/招生计划_2026_{ke}类.csv', encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                k = (r['院校招生代码'], str(int(r['专业组代码'])))
                cur = out.setdefault(k, {'xk': r['选科要求'], 'majors': []})
                fee = r['学费'].strip()
                cur['majors'].append([
                    r['专业名称'].strip(),
                    int(r['计划人数']) if r['计划人数'].isdigit() else 0,
                    int(fee) if fee.isdigit() else fee,
                    r['学制'].strip(),
                    r['专业备注'].strip()])
    return out

def load_tags():
    out = {}
    with open(f'{DATA}/prod/院校/院校主档.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            tags = [t for t in re.split(r'[·；/]', r['院校标签']) if t in ('985', '211', '双一流')]
            if tags: out[r['院校名称']] = tags
    return out

def load_rates():
    """三率 → {校名: (落实率, 保研率)}（届别进全局注，不上瓦片）"""
    out = {}
    with open(f'{DATA}/prod/院校/院校三率.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            ly, by = r['落实率%'].strip(), r['保研率%'].strip()
            if ly or by: out[r['院校名称']] = (ly, by)
    return out

def load_jobs():
    """就业链：专业名 → (careers, top职业类, 半年, 五年)"""
    c2m = {}
    with open(f'{DATA}/staging/就业_职业词_职业类映射_v0.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            c2m[r['职业词']] = (r['麦可思职业类'], r['映射类型'])
    pay = {}
    with open(f'{DATA}/staging/就业_职业类薪酬_麦可思2026_v0.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            pay[r['职业类']] = (r['半年薪_2025届'], r['五年薪_2020届'])
    avg = pay.get('全国本科', ('6435', ''))[0]
    majors = {}
    for ln in open(f'{DATA}/raw/专业岗位映射_阳光高考_2026-09/zy_map_records.jsonl'):
        r = json.loads(ln)
        clss = collections.Counter()
        for c in r.get('careers', []):
            cls, typ = c2m.get(c, ('', ''))
            if typ == '麦可思职业类': clss[cls] += 1
        top = clss.most_common(1)
        p = pay.get(top[0][0], ('', '')) if top else ('', '')
        majors[r['name']] = {'careers': r.get('careers', []), 'h': p[0], 'y': p[1]}
    return majors, avg

def fee_fmt(v):
    return f'{v:,}' if isinstance(v, int) else str(v)

def main():
    yfd_p = load_yfd(f'{DATA}/prod/一分一段/shanxi_2026_yifenyiduan_物理类.csv')
    yfd_h = load_yfd(f'{DATA}/prod/一分一段/shanxi_2026_yifenyiduan_历史类.csv')
    print(f'一分一段: 物理{len(yfd_p)} 历史{len(yfd_h)}')

    p26, d1 = parse_pdf(f'{DATA}/raw/官方PDF_2026本科批投档线_物理类.pdf', yfd_p)
    h26, d2 = parse_pdf(f'{DATA}/raw/官方PDF_2026本科批投档线_历史类.pdf', yfd_h)
    print(f'2026转录: 物理{len(p26)}(剔{d1}) 历史{len(h26)}(剔{d2})')

    p25 = []
    with open(f'{DATA}/prod/投档线/投档线_山西_2025_物理类_位次补全.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if EXCLUDE.search(r['专业组']): continue
            g = GNUM.search(r['专业组'])
            code = r.get('院校代号') or r.get('﻿院校代号')
            p25.append({'n': r['院校名称'].strip(), 'g': str(int(g.group(1))), 's': int(r['投档最低分']), 'r': int(r['最低位次_一分一段查表'])})
    print(f'2025物理: {len(p25)}')

    def merge(rows26, rows25):
        db = {}
        for r in rows26:
            db[(r['n'], r['g'])] = {'n': r['n'], 'c': r['c'], 'g': r['g'], 'gt': r['gt'], 's26': r['s'], 'r26': r['r']}
        for r in rows25:
            k = (r['n'], r['g'])
            if k in db: db[k]['s25'], db[k]['r25'] = r['s'], r['r']
        out = [o for o in db.values() if o.get('s26')]
        for o in out: o['r'] = o.get('r26') or o.get('r25')
        out.sort(key=lambda o: o['r'])
        return out

    phy, his = merge(p26, p25), merge(h26, [])
    print(f'合并: 物理{len(phy)} 历史{len(his)}')

    plans, tags, rates, (jobs, AVG) = load_plans(), load_tags(), load_rates(), load_jobs()

    detail, no_plan = {}, 0
    def enrich(rows, kelei):
        nonlocal no_plan
        packed = []
        for o in rows:
            k = (o['c'], o['g'])
            pl = plans.get(k)
            xk = pl['xk'] if pl else ''
            majors = sorted(pl['majors'], key=lambda m: -m[1]) if pl else []
            if not pl: no_plan += 1
            # 小卡行2 外漏（≤6 短名）
            short = [PAREN.sub('', m[0]) for m in majors[:6]]
            mc = len(majors)
            brief = '·'.join(short) + (f' 等{mc}个专业' if mc > 6 else (f' 共{mc}个专业' if mc else ''))
            # 就业链聚合
            chips, hs, ys = collections.Counter(), [], []
            for m in majors:
                jb = jobs.get(PAREN.sub('', m[0]).strip())
                if not jb: continue
                for c in jb['careers']:
                    c2 = re.sub(r'\(.*?\)', '', c)
                    chips['公务员' if c2.startswith('公务员') else c2] += 1
                if jb['h']: hs.append(int(jb['h']))
                if jb['y']: ys.append(int(jb['y']))
            chip_s = '|'.join(c for c, _ in chips.most_common(5))
            pay = f'{min(hs)}-{max(hs)}|{min(ys)}-{max(ys)}' if hs and ys else ''
            # tips
            tips = []
            fees = [m[2] for m in majors if isinstance(m[2], int)]
            if majors and all(re.search(r'试验班|荣誉班|精英班|创新班', m[0]) for m in majors):
                tips.append('G:✓ 全组试验班/特色班，进组即好专业')
            if fees and max(fees) - min(fees) > 2000:
                hi = max(fees)
                n_hi = sum(1 for f in fees if f == hi)
                tips.append(f'Y:⚠ {n_hi} 个名额学费 {fee_fmt(hi)} 元/年（已标黄），其余 {fee_fmt(min(fees))} 元')
            body = [m for m in majors if re.search(r'色盲|色弱|身高', m[4])]
            if body:
                tips.append(f'Y:⚠ {len(body)} 个专业限色盲色弱等身体条件，报前核对')
            campus_set = {re.search(r'办学地点([^;；,，]*)', m[4]).group(1) for m in majors if re.search(r'办学地点([^;；,，]*校区)', m[4])}
            if len(campus_set) > 1:
                tips.append('Y:⚠ 组内专业分属不同校区，报前看备注')
            total = sum(m[1] for m in majors)
            if majors and total <= 10:
                tips.append(f'Y:⚠ 全组仅 {total} 人，分数线波动可能大')
            tag = '/'.join(tags.get(o['n'], []))
            ly, by = rates.get(o['n'], ('', ''))
            gd = f'第{o["g"]}组[{xk}]' if xk else o['gt']
            if '合作' in o['gt']: gd += '·合作'
            dkey = f'{kelei}|{o["c"]}|{o["g"]}'
            if majors: detail[dkey] = majors
            # [n,gd,r,s26,r26,s25,r25,tag,xk,brief,mc,ly,by,chips,pay,tips,dkey]
            packed.append([o['n'], gd, o['r'], o.get('s26', 0), o.get('r26', 0), o.get('s25', 0), o.get('r25', 0),
                           tag, xk, brief, mc, ly, by, chip_s, pay, '|'.join(tips[:2]), dkey])
        return packed

    phy_p, his_p = enrich(phy, '物理'), enrich(his, '历史')
    hit_rate = sum(1 for r in phy_p + his_p if r[11] or r[12])
    print(f'三率命中 {hit_rate} 条；无计划明细 {no_plan} 组')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('// 全量院校库（build_db.py v2 生成，勿手改）——v5卡：两年组线+计划+三率+就业链+tips；剔专项/预科/民族班/定向\n')
        f.write('const DB_PHY=' + json.dumps(phy_p, ensure_ascii=False, separators=(',', ':')) + ';\n')
        f.write('const DB_HIS=' + json.dumps(his_p, ensure_ascii=False, separators=(',', ':')) + ';\n')
        f.write(f'const PAY_AVG="{AVG}";\n')
    with open(OUT_D, 'w', encoding='utf-8') as f:
        f.write('// 组内专业明细（按需加载）——[专业,人数,学费,学制,备注]\n')
        f.write('const DB_DETAIL=' + json.dumps(detail, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print(f'输出 {OUT} {os.path.getsize(OUT)//1024}KB / {OUT_D} {os.path.getsize(OUT_D)//1024}KB')

if __name__ == '__main__':
    main()
