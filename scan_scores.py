#!/usr/bin/env python3
# 全分数段扫描：高占比区间每一分（文/理）× 三档，校验结果页与需求一致性 + 家长视角数据采集。
# 用法：python3 scan_scores.py  （需先起本地服务：python3 -m http.server 8321）
# 产物：scan_report.json（明细，已 gitignore）+ stdout Markdown 汇总
# 与 selftest.py 分工：selftest=定点深检（含真点击交互）；本脚本=全段扫面+跨分稳定性+家长视角聚合。
import json, re, sys
from collections import Counter, defaultdict
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:8321/'
COVER = 0.80          # 覆盖本科线上 80% 考生的分数带
BAND = 20             # 聚合分数带宽
SNAP_PHY = [450, 500, 550, 600]   # 跨分段对照快照
SNAP_HIS = [460, 500, 540]
JITTER_WIN = ('物理', 575, 585)   # 相邻分稳定性明细窗口

per_score = []   # 每分事实包
fails = []       # 一致性违例

def F(scene, check, detail=''):
    fails.append({'scene': scene, 'check': check, 'detail': str(detail)[:200]})

JS_RANGE = """(track) => {
  const t = track==='物理'?YF_PHY:YF_HIS, total = ONLINE_2026[track];
  const lo = t.base;                       // 本科线（表尾=线上人数）
  let hi = lo;
  for (let s = lo; s <= t.topAt; s++) {
    if (t.cum[lo-t.base] - t.cum[s-t.base] >= total*""" + str(COVER) + """) { hi = s; break; }
    hi = s;
  }
  return {lo, hi, covered: t.cum[0]-t.cum[hi-t.base], total};
}"""

# 每分一次注入，三档事实包全采
JS_SCORE = """([first, score]) => {
  st.first=first; st.gender='男'; st.score=String(score); st.career='还没想好'; st.region='还没想好';
  estimate = {est: scoreToRank(score, first), widen:1, basis:'按成绩估算 · 上下浮动约 15 分',
              lo: scoreToRank(score+15, first), hi: scoreToRank(score-15, first)};
  document.querySelectorAll('.screen').forEach(s=>s.classList.remove('on'));
  document.getElementById('s3').classList.add('on');
  render();
  const out = {
    locateText: document.getElementById('locateText').textContent,
    locateSub: document.getElementById('locateSub').textContent,
    hasLab: !!document.querySelector('.locate .lab'),
    hasCTA: !!document.querySelector('.locate a[onclick*="goLead"]'),
    tiersOut: document.getElementById('tiers').textContent.includes('综合排序='),
    tiers: [],
  };
  for (let ti=0; ti<3; ti++) {
    switchTier(ti);
    const body = document.getElementById('tierBody');
    const t = window._tierData[ti];
    const f = {name: t.name, rows: t.rows.length};
    if (!t.rows.length) { f.empty=true; out.tiers.push(f); continue; }
    const cls = t.cls;
    const topEls = [...body.querySelectorAll(':scope > .tier.'+cls)];
    const rows = t.rows, top = pickTop3(rows);
    const pure=[]; const seen=new Set();
    for(const r of rows){ if(pure.length>=3) break; if(seen.has(r[0])) continue; seen.add(r[0]); pure.push(r); }
    const byScore = [...rows].sort((a,b)=>scoreOf(b,estimate.est)-scoreOf(a,estimate.est));
    // 槽位豁免+层次参照（参照卡可能已被前槽消费，此时合法底线=剩余可用最高层次）
    const comp=[pure[0]], cats0=new Set([headCat(pure[0])]), used=new Set([pure[0][0]]);
    f.slotHad=[]; f.availMaxLvl=[];
    for(let slot=1; slot<3; slot++){
      let found=false, amax=-1;
      for(const r of rows){ if(!used.has(r[0]) && lvlOf(r)>amax) amax=lvlOf(r); }
      f.availMaxLvl.push(amax);
      for(const r of rows.slice(0,10)){
        if(used.has(r[0])) continue;
        if(lvlOf(r)<lvlOf(pure[slot]||pure[0])) continue;
        const c=headCat(r); if(c==='其他'||cats0.has(c)) continue;
        found=true; break;
      }
      f.slotHad.push(found);
      const real=top[slot]; if(!real) break;
      cats0.add(headCat(real)); used.add(real[0]);
    }
    f.topCount = topEls.length;
    f.topNames = top.map(r=>r[0]+r[1]);
    f.topCats = top.map(headCat);
    f.topLvls = top.map(lvlOf);
    f.topTags = top.map(r=>r[7]||'');
    f.topIsRows0 = top[0]===rows[0];
    f.topLvlsActual = top.map(lvlOf);
    f.pureLvls = pure.map(lvlOf);
    const c10={}; for(const r of rows.slice(0,10)){ c10[r[0]]=(c10[r[0]]||0)+1; }
    f.top10maxSame = Math.max(...Object.values(c10));
    f.capPermOK = rows.length===byScore.length && rows.every(r=>byScore.includes(r));
    const t10=rows.slice(0,10);
    f.displacedOK = byScore.slice(0,10).filter(r=>!t10.includes(r))
      .every(r=> t10.filter(x=>x[0]===r[0]).length>=2);
    const tail=rows.slice(10);
    const extra=tail.filter(r=>byScore.slice(0,10).includes(r));
    const restRows=tail.filter(r=>!extra.includes(r));
    const expectRest=byScore.slice(10).filter(r=>!t10.includes(r));
    const descOK=a=>a.every((r,i)=>i===0||scoreOf(a[i-1],estimate.est)>=scoreOf(r,estimate.est));
    f.tailOrderOK = descOK(extra) && restRows.length===expectRest.length && restRows.every((r,i)=>r===expectRest[i]);
    f.l234OK = topEls.every(e=>['c-l2','c-l3','c-l4'].every(c=>!!e.querySelector('.'+c)));
    f.borderOK = topEls.every(e=>getComputedStyle(e.querySelector('.card.nc')).borderLeftColor!=='rgba(0, 0, 0, 0)');
    f.reasons = topEls.map(e=>{const r=e.querySelector('.c-reason'); return r?r.textContent:'';});
    f.coldChip = body.querySelectorAll('.heat.cold').length;
    f.coldText = (body.textContent.match(/冷/g)||[]).length;
    f.warnBad = [...document.querySelectorAll('.pill.warn')].filter(p=>!p.textContent.startsWith('小提示：')||p.textContent.includes('⚠')).length;
    f.hintBad = body.textContent.includes('建议本档挑');
    // 展开全部结构
    const toggle = body.querySelector('.opt-toggle');
    if (toggle) {
      const rl = body.querySelector('.rest-list');
      const rt = [...rl.querySelectorAll(':scope > .tier')];
      f.restCards = rt.filter(x=>x.querySelector('.card.nc')).length;
      f.restLines = rl.querySelectorAll('.dbline').length;
      f.restTotal = rows.length - 3;
    }
    out.tiers.push(f);
  }
  return out;
}"""

def check_one(first, score, f):
    s = f'{first}{score}'
    if not re.match(rf'^{score} 分 · 全省{first}类约前 [\d,]+ ~ [\d,]+ 名$', f['locateText']):
        F(s, '定位卡大字格式', f['locateText'])
    if f['hasLab'] or f['hasCTA']: F(s, '定位卡标题/引导链残留')
    if f['tiersOut']: F(s, '页底排序说明残留')
    for t in f['tiers']:
        sc = f'{s}/{t["name"]}'
        if t.get('empty'): continue
        if t['topCount'] != 3: F(sc, 'TOP3=3卡', t['topCount'])
        if not t['topIsRows0']: F(sc, 'TOP3#1=S第一')
        if len(set(n.split('第')[0] for n in t['topNames'])) != len(t['topNames']): F(sc, 'TOP3同校去重', t['topNames'])
        used = set();
        for i, c in enumerate(t['topCats']):
            if c == '其他': continue
            if c in used and i > 0 and t['slotHad'][i-1]:
                F(sc, '角色撞车且有候选未用', f"cats={t['topCats']} slotHad={t['slotHad']}")
            used.add(c)
        for i in (1, 2):  # 层次守卫：参照卡若已被前槽消费，合法底线=剩余可用最高层次
            if i < len(t['topLvlsActual']):
                base = t['pureLvls'][i] if i < len(t['pureLvls']) else t['pureLvls'][0]
                if t['topLvlsActual'][i] < min(base, t['availMaxLvl'][i-1]):
                    F(sc, '层次守卫', f"top={t['topLvlsActual']} pure={t['pureLvls']} avail={t['availMaxLvl']}")
        if t['top10maxSame'] > 2: F(sc, '前10同校>2', t['top10maxSame'])
        if not t['capPermOK']: F(sc, '档内行排列异常')
        if not t['displacedOK']: F(sc, '被挤出者非限流原因')
        if not t['tailOrderOK']: F(sc, '10名后顺序异常')
        if not t['l234OK']: F(sc, 'TOP3卡行件缺失')
        if not t['borderOK']: F(sc, '档位色条透明')
        for i, rs in enumerate(t['reasons']):
            if rs and (rs.count('·') > 1 or '⚠' in rs or '冷' in rs):
                F(sc, f'推荐理由违规(top#{i+1})', rs)
        if t['coldChip'] or t['coldText']: F(sc, '冷标签残留', f"chip={t['coldChip']} text={t['coldText']}")
        if t['warnBad']: F(sc, '灰签格式违规', t['warnBad'])
        if t['hintBad']: F(sc, '档引导残留')
        if 'restTotal' in t:
            exp = min(5, t['restTotal'])
            if t['restCards'] != exp or t['restLines'] != t['restTotal'] - exp:
                F(sc, '展开全部结构', f"cards={t['restCards']}/{exp} lines={t['restLines']}/{t['restTotal']-exp}")

def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 480, 'height': 900})
        pg.goto(BASE); pg.wait_for_timeout(1500)

        # A. 区间自动框定
        ranges = {}
        for track in ('物理', '历史'):
            r = pg.evaluate(JS_RANGE, track)
            ranges[track] = r
            print(f"[区间] {track}: {r['lo']}-{r['hi']} 分（覆盖线上 {r['covered']:,}/{r['total']:,} 人 = {r['covered']/r['total']*100:.1f}%）")

        # B. 逐分扫描
        for track in ('物理', '历史'):
            lo, hi = ranges[track]['lo'], ranges[track]['hi']
            for score in range(lo, hi+1):
                f = pg.evaluate(JS_SCORE, [track, score])
                f['track'] = track; f['score'] = score
                per_score.append(f)
                check_one(track, score, f)
            print(f"[扫描] {track} {lo}-{hi} 完成（{hi-lo+1} 分 × 3 档）")

        # C2. 跨分段快照
        snaps = {}
        for track, scores in (('物理', SNAP_PHY), ('历史', SNAP_HIS)):
            for score in scores:
                f = pg.evaluate(JS_SCORE, [track, score])
                snaps[f'{track}{score}'] = [
                    {'school': t['topNames'], 'cats': t['topCats'], 'lvls': t['topLvls'],
                     'reasons': t['reasons'], 'rows': t['rows']}
                    for t in f['tiers'] if not t.get('empty')]
        b.close()

    # ===== Python 侧聚合 =====
    # 跨分单调性：位次区间随分数递减
    by_track = defaultdict(list)
    for f in per_score: by_track[f['track']].append(f)
    for track, lst in by_track.items():
        lst.sort(key=lambda x: x['score'])
        for a, b_ in zip(lst, lst[1:]):
            ma = re.search(r'前 ([\d,]+) ~', a['locateText']); mb = re.search(r'前 ([\d,]+) ~', b_['locateText'])
            if ma and mb and int(mb.group(1).replace(',','')) >= int(ma.group(1).replace(',','')):
                F(f'{track}{b_["score"]}', '位次区间非单调', f"{a['score']}→{b_['score']}: {ma.group(1)}→{mb.group(1)}")

    # C1. 相邻分 TOP3 换卡率（全区间）
    jitter = defaultdict(list)  # track → [(score, tier, 换卡数)]
    for track, lst in by_track.items():
        for a, b_ in zip(lst, lst[1:]):
            for ta, tb in zip(a['tiers'], b_['tiers']):
                if ta.get('empty') or tb.get('empty'): continue
                diff = len(set(ta['topNames']) - set(tb['topNames']))
                cnt_diff = abs(ta['rows'] - tb['rows'])
                jitter[track].append({'from': a['score'], 'to': b_['score'], 'tier': ta['name'],
                                      'swap': diff, 'cntDiff': cnt_diff})
    # 换卡≥2 的抖动点
    jitter_hot = [j for js in jitter.values() for j in js if j['swap'] >= 2]

    # D. 分数带聚合
    bands = defaultdict(lambda: {'lvls': Counter(), 'cats': Counter(), 'scores': 0, 'empty': 0})
    for f in per_score:
        band = f"{f['track']}{f['score']//BAND*BAND}-{f['score']//BAND*BAND+BAND-1}"
        bands[band]['scores'] += 1
        for t in f['tiers']:
            if t.get('empty'): bands[band]['empty'] += 1; continue
            for l in t['topLvls']: bands[band]['lvls'][l] += 1
            for c in t['topCats']: bands[band]['cats'][c] += 1

    report = {'ranges': ranges, 'fails': fails, 'jitterHot': jitter_hot,
              'bands': {k: {'lvls': dict(v['lvls']), 'cats': dict(v['cats']),
                            'scores': v['scores'], 'empty': v['empty']} for k, v in bands.items()},
              'snaps': snaps,
              'jitterWin': [j for j in jitter[JITTER_WIN[0]] if JITTER_WIN[1] <= j['from'] <= JITTER_WIN[2]],
              'perScore': per_score}
    with open('scan_report.json', 'w') as fp:
        json.dump(report, fp, ensure_ascii=False, indent=1)

    # stdout 汇总
    print(f"\n===== 扫描完成：{len(per_score)} 分点，违例 {len(fails)} 条 =====")
    byc = Counter(x['check'] for x in fails)
    for k, v in byc.most_common(): print(f"  {k}: {v} 次")
    print(f"抖动点（相邻分 TOP3 换≥2 卡）: {len(jitter_hot)} 处")
    print("明细 scan_report.json")

if __name__ == '__main__':
    main()
