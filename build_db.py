#!/usr/bin/env python3
# build_db.py v2 — v5 推荐卡落地：两年组线+计划明细+三率+就业链+tips → db.js + db_detail.js
# 数据红线：专项计划/预科/民族班/定向不进推荐池；薪酬=全国类级（麦可思2026）非校级
import re, json, csv, os, collections
import pdfplumber

DATA = '/Users/jediyang/ClaudeCode/sghdx/data'
OUT = '/Users/jediyang/ClaudeCode/sghdx/gaokao-demo/db.js'
OUT_D = '/Users/jediyang/ClaudeCode/sghdx/gaokao-demo/db_detail.js'

T0 = {'北京', '上海', '广州', '深圳'}
T1 = {'天津', '苏州', '南京', '杭州', '宁波', '无锡', '厦门', '青岛', '佛山', '长沙', '武汉'}
NORTH = {'山西', '北京', '天津', '河北', '山东', '河南', '陕西', '辽宁', '吉林', '黑龙江', '内蒙古', '甘肃', '宁夏', '新疆', '青海'}
SOE_CLS = {'电力/能源', '矿山/石油', '建筑工程', '交通运输/邮电', '机械/仪器仪表', '测绘', '冶金材料'}
ENG_CLS = {'互联网开发及应用', '计算机与数据处理', '电气/电子（不包括计算机）', '机械/仪器仪表', '电力/能源'}
EDU_CLS = {'中小学教育', '中等职业教育', '幼儿与学前教育', '职业培训/其他教育'}
MED_CLS = {'医疗保健/紧急救助'}
RES_CLS = {'研究人员'}

def t_level(prov, city):
    if city in T0: return 0
    if city in T1: return 1
    if prov == '山西': return 2
    if prov in NORTH: return 3
    return 4

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
    out, geo = {}, {}
    with open(f'{DATA}/prod/院校/院校主档.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            tags = [t for t in re.split(r'[·；/]', r['院校标签']) if t in ('985', '211', '双一流')]
            if tags: out[r['院校名称']] = tags
            geo[r['院校名称']] = (r['所在省份'], r['所在城市'])
    return out, geo

def load_heat():
    """热度标签 → {专业: (auto标签, 冷权重, 热权重)}；auto×1.0 / review×0.5"""
    out = {}
    with open(f'{DATA}/staging/就业_专业热度标签_Jev2026_v0.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            cls, dec = r['热度标签'], r['裁定']
            w = 1.0 if dec == 'auto' else 0.5
            out[r['专业名称']] = (cls if dec == 'auto' else '',
                                  w if cls == '冷门' else 0,
                                  w if cls == '热门' else 0)
    return out

def load_rates(tags):
    """三率 → {校名: (落实率, 保研率)}；非92且保研率>15%=疑似升学率串数，隔离待复核（2026-09-27 排序上线时实证）"""
    out, quarantine = {}, []
    with open(f'{DATA}/prod/院校/院校三率.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            ly, by = r['落实率%'].strip(), r['保研率%'].strip()
            base = PAREN.sub('', r['院校名称'])
            tb = getattr(load_rates, '_tb', None)
            if tb is None:
                tb = {PAREN.sub('', k) for k in tags}
                load_rates._tb = tb
            is92 = tags.get(r['院校名称']) or base in tb or any(x and (x in base or base in x) for x in tb)
            if by and float(by) > 15 and not is92:
                quarantine.append((r['院校名称'], by))
                by = ''
            if ly or by: out[r['院校名称']] = (ly, by)
    print(f'保研率隔离 {len(quarantine)} 校（非92且>15%）: {[q[0] for q in quarantine]}')
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
    avg_h, avg_y = pay.get('全国本科', ('6435', '10664'))  # 麦可思2026全国本科：半年6435/五年10664（同表第43行）
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
    return majors, avg_h, avg_y, c2m

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

    plans, (tags, geo) = load_plans(), load_tags()
    rates = load_rates(tags)
    heat = load_heat()
    jobs, AVG, AVG5, c2m = load_jobs()
    AVG_N, AVG5_N = int(AVG), int(AVG5)

    detail, no_plan = {}, 0
    # v3.3.2: 括号方向优先匹配（Jedi 抓案：信管（医学信息学方向）被按基名配了工程造价）
    # 退化链：括号方向词（去方向/班后缀、去工程技术归一）查库 → 基名 → 不显示
    def norm_dir(s):
        return re.sub(r'(方向|实验班|试验班|创新班|精英班|荣誉班|特色班|计划|班)$', '', s).replace('工程', '').replace('技术', '').strip()
    def find_jobs(name):
        base = PAREN.sub('', name).strip()
        pm = re.search(r'[（(]([^）)]*)[）)]', name)
        if pm:
            d, dn = pm.group(1).strip(), norm_dir(pm.group(1))
            if dn and dn != base:
                if d in jobs: return jobs[d]
                if len(dn) >= 3:
                    for k in jobs:
                        kn = k.replace('工程', '').replace('技术', '')
                        if dn == kn or (len(kn) >= 3 and (dn in kn or kn in dn)):
                            return jobs[k]
        return jobs.get(base)
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
            cls_cnt, gz_cnt, total_cnt = collections.Counter(), 0, 0
            for m in majors:
                jb = find_jobs(m[0])
                if not jb: continue
                for c in jb['careers']:
                    c2 = re.sub(r'\(.*?\)', '', c)
                    chips['公务员' if c2.startswith('公务员') else c2] += 1
                    total_cnt += 1
                    if c2.startswith('公务员') or c2 == '事业单位人员': gz_cnt += 1
                    cls, typ = c2m.get(c, ('', ''))
                    if typ == '麦可思职业类': cls_cnt[cls] += 1
                if jb['h']: hs.append(int(jb['h']))
                if jb['y']: ys.append(int(jb['y']))
            chip_s = '|'.join(c for c, _ in chips.most_common(5))
            pay = f'{min(hs)}-{max(hs)}|{min(ys)}-{max(ys)}' if hs and ys else ''
            gz = round(100 * gz_cnt / total_cnt) if total_cnt else 0
            cls_total = sum(cls_cnt.values()) or 1
            edu = 1 if sum(cls_cnt[c] for c in EDU_CLS) / cls_total >= 0.15 else 0
            med = 1 if sum(cls_cnt[c] for c in MED_CLS) / cls_total >= 0.15 else 0
            res = 1 if sum(cls_cnt[c] for c in RES_CLS) / cls_total >= 0.08 else 0
            soe = 1 if sum(cls_cnt[c] for c in SOE_CLS) / cls_total >= 0.30 else 0
            eng = 1 if sum(cls_cnt[c] for c in ENG_CLS) / cls_total >= 0.30 else 0
            # tips
            tips = []
            fees = [m[2] for m in majors if isinstance(m[2], int)]
            if majors and all(re.search(r'试验班|荣誉班|精英班|创新班', m[0]) for m in majors):
                tips.append('G:✓ 全组试验班/特色班，进组即好专业')
            if fees and max(fees) - min(fees) > 2000:
                hi = max(fees)
                n_hi = sum(1 for f in fees if f == hi)
                wan = lambda v: f'{v/10000:g} 万' if v >= 10000 else f'{v:,} 元'
                tips.append(f'Y:{n_hi} 个名额学费 {wan(hi)}/年，其余为 {wan(min(fees))}')
            body = [m for m in majors if re.search(r'色盲|色弱|身高', m[4])]
            if body:
                tips.append(f'Y:{len(body)} 个专业有色盲色弱等身体限制')
            campus_set = {re.search(r'办学地点([^;；,，]*)', m[4]).group(1) for m in majors if re.search(r'办学地点([^;；,，]*校区)', m[4])}
            if len(campus_set) > 1:
                tips.append('Y:组内专业在不同校区上课')
            total = sum(m[1] for m in majors)
            if majors and total <= 10:
                tips.append(f'Y:全组仅 {total} 人，分数线历年波动较大')
            tag = '/'.join(tags.get(o['n'], []))
            ly, by = rates.get(o['n'], ('', ''))
            gd = f'第{o["g"]}组[{xk}]' if xk else o['gt']
            if '合作' in o['gt']: gd += '·合作'
            dkey = f'{kelei}|{o["c"]}|{o["g"]}'
            if majors: detail[dkey] = majors
            # ── 组热度（auto×1.0+review×0.5 计划加权；Jedi 2026-09-27 拍板）──
            total_plan = sum(m[1] for m in majors) if majors else 0
            hw_c = hw_h = 0
            for m in majors:
                lab, wc, wh = heat.get(PAREN.sub('', m[0]).strip(), ('', 0, 0))
                m.append(lab)
                hw_c += wc * m[1]
                hw_h += wh * m[1]
            hot_sh = hw_h / total_plan if total_plan else 0
            cold_sh = hw_c / total_plan if total_plan else 0
            heat_score = 0
            if hot_sh >= 0.7:
                heat_score += 4
                tips.append('G:✓ 全组热门专业（就业热度高置信标注）')
            # S-Score v2.0：冷占比线性静默扣分（−占比×10 封顶−8），页面零提示（合规：冷字样不上网页）
            heat_score -= min(8, cold_sh * 10)

            # ── 静态排序分 S-Score v2.0（2026-09-27 Jedi 拍板）──
            s_lvl = 30 if '985' in tag else (20 if '211' in tag else (10 if '双一流' in tag else 0))
            s_by = float(by) * 1.0 if by else 0  # v2.0: ×1.5→×1.0（远变量+口径风险降权）
            if '合作' in gd: s_by *= 0.5  # v2.0: 合作组保研折半（项目实际保研远低于全校口径）
            s_ly = (float(ly) - 80) * 0.5 if ly else 0
            import statistics
            h_med = statistics.median(hs) if hs else AVG_N
            y5_med = statistics.median(ys) if ys else None
            if y5_med is not None:  # v2.0: 半年×0.6+五年×0.4（后劲型专业不再被低估）
                s_pay = ((h_med - AVG_N) * 0.6 + (y5_med - AVG5_N) * 0.4) / 1000 * 2
            else:
                s_pay = (h_med - AVG_N) / 1000 * 2
            total_plan = sum(m[1] for m in majors)
            risk = 0
            if len(majors) == 1: risk -= 4  # C: 单专业组=无调剂余地+波动
            if majors and total_plan <= 10: risk -= 4
            fee_list = [m[2] for m in majors if isinstance(m[2], int)]
            if fee_list:  # v2.0: 学费阶梯（原一刀切≥1.3万−5 扣轻了）
                fee_med = statistics.median(fee_list)
                if fee_med >= 50000: risk -= 15
                elif fee_med >= 30000: risk -= 10
                elif fee_med >= 13000: risk -= 5
            stab = 0
            # A: 重排组识别——25/26 同号组位次比 >2x 或 <0.5x（Jedi 2026-09-27 拍板）
            if o.get('s25') and o.get('r25') and o.get('r26'):
                ratio = o['r26'] / o['r25']
                if ratio > 2 or ratio < 0.5:
                    risk -= 6
                    tips.append('Y:该组 25 年构成不同，两年分数不可直接比')
                elif 0.8 <= ratio <= 1.25:
                    stab = 3  # v2.0: 稳定性因子——两年线互证，新高考首年可信度加分
            # B: 偏远心智扣分（山西家长视角，Jedi 拍板）
            if re.search(r'西藏|新疆|青海|甘肃|宁夏|内蒙古', geo.get(o['n'], ('', ''))[0]):
                risk -= 6
            plan_bonus = 2 if total_plan >= 50 else 0  # v2.0: 大计划正向端（线稳敢报）
            sb = round(s_lvl + s_by + s_ly + s_pay + risk + heat_score + stab + plan_bonus, 1)
            prov, city = geo.get(o['n'], ('', ''))
            tl = t_level(prov, city)
            vtag = '职业本科' if '职业技术大学' in o['n'] else ''
            packed.append([o['n'], gd, o['r'], o.get('s26', 0), o.get('r26', 0), o.get('s25', 0), o.get('r25', 0),
                           tag, xk, brief, mc, ly, by, chip_s, pay, '|'.join(tips[:2]), dkey,
                           sb, prov, city, tl, gz, edu, med, res, soe, eng, vtag])
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
