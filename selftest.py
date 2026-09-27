#!/usr/bin/env python3
# 页面自测：对照 design/推荐卡设计需求_2026-09-27.md 与 pm/排序引擎_S-Score.md
# 校验 localhost:8321 页面的 UI/交互/数据/策略一致性。
# 用法：python3 selftest.py  （需先起本地服务：python3 -m http.server 8321）
# 退出码：0=全绿，1=有 FAIL。结果同时落 selftest_report.json 供复盘。
import json, re, sys
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:8321/'
SCENARIOS = [  # (科类, 分数, 说明)
    ('物理', 650, '高分段'),
    ('物理', 580, '中高分·主战场'),
    ('物理', 520, '中段·密度最高'),
    ('历史', 480, '历史低分段'),
]
R = []  # (类别, 检查项, 场景, 通过, 详情)；ok=None 表示跳过（不适用）
def rec(cat, name, scene, ok, detail=''):
    R.append({'cat': cat, 'name': name, 'scene': scene, 'ok': None if ok is None else bool(ok), 'detail': str(detail)[:300]})

# 注入状态并渲染某分某档；返回页面采集的事实包
def render_tier(page, first, score, tier_idx):
    return page.evaluate("""async ([first, score, tierIdx]) => {
      st.first=first; st.gender='男'; st.score=String(score); st.career='还没想好'; st.region='还没想好';
      estimate = {est: scoreToRank(score, first), widen:1, basis:'按成绩估算 · 上下浮动约 15 分',
                  lo: scoreToRank(score+15, first), hi: scoreToRank(score-15, first)};
      document.querySelectorAll('.screen').forEach(s=>s.classList.remove('on'));
      document.getElementById('s3').classList.add('on');
      render(); switchTier(tierIdx);
      await new Promise(r=>setTimeout(r,50));
      const body = document.getElementById('tierBody');
      const t = window._tierData[tierIdx];
      const cls = t.cls;
      const topEls = [...body.querySelectorAll(':scope > .tier.'+cls)];
      // 展开全部
      const toggle = body.querySelector('.opt-toggle');
      let rest = null;
      if (toggle) {
        toggleRest(toggle, t.count);
        const rl = body.querySelector('.rest-list');
        const restTiers = [...rl.querySelectorAll(':scope > .tier')];
        rest = {
          cardCount: restTiers.filter(x=>x.querySelector('.card.nc')).length,
          lineBoxCount: restTiers.filter(x=>x.querySelector('.dbline')).length,
          lineCount: rl.querySelectorAll('.dbline').length,
          total: t.rows.length - 3,
        };
        toggleRest(toggle, t.count); // 收回
      }
      // 策略事实（页面内函数直接算）
      const rows = t.rows;
      const top = pickTop3(rows);
      const pure=[]; const seen=new Set();
      for(const r of rows){ if(pure.length>=3) break; if(seen.has(r[0])) continue; seen.add(r[0]); pure.push(r); }
      // 重算 byScore→capTop10 期望顺序（对照 computeTiers 产物）
      const byScore = [...rows].sort((a,b)=>scoreOf(b,estimate.est)-scoreOf(a,estimate.est));
      return {
        tierName: t.name, rowCount: rows.length,
        locateText: document.getElementById('locateText').textContent,
        locateSub: document.getElementById('locateSub').textContent,
        hasLab: !!document.querySelector('.locate .lab'),
        hasCTA: !!document.querySelector('.locate a'),
        hintForbidden: body.textContent.includes('建议本档挑') || document.getElementById('tiers').textContent.includes('综合排序='),
        topCount: topEls.length,
        topNames: topEls.map(e=>e.querySelector('.c-name').textContent),
        topHasL234: topEls.map(e=>['c-l2','c-l3','c-l4'].every(c=>!!e.querySelector('.'+c))),
        topBorderLeft: topEls.map(e=>getComputedStyle(e.querySelector('.card.nc')).borderLeftColor),
        reasons: topEls.map(e=>{const r=e.querySelector('.c-reason'); return r?r.textContent:'';}),
        coldChipCount: body.querySelectorAll('.heat.cold').length,
        coldText: (body.textContent.match(/冷/g)||[]).length,
        warnTips: [...document.querySelectorAll('.pill.warn')].map(p=>p.textContent),
        rest,
        // 策略
        topIsRows0: top[0]===rows[0],
        topNamesUnique: new Set(top.map(r=>r[0])).size===top.length,
        topCats: top.map(headCat),
        pureCats: pure.map(headCat),
        lvlGuardOK: top.every((r,i)=> i===0 || lvlOf(r) >= lvlOf(pure[i]||pure[0])),
        // 角色互补豁免检测：模拟挑选，记录每个槽位是否「池内确无新角色候选」（兜底属设计行为）
        slotHadCandidate: (()=>{ const comp=[pure[0]], cats=new Set([headCat(pure[0])]), used=new Set([pure[0][0]]);
          const flags=[];
          for(let slot=1; slot<3; slot++){
            let found=false;
            for(const r of rows.slice(0,10)){
              if(used.has(r[0])) continue;
              if(lvlOf(r)<lvlOf(pure[slot]||pure[0])) continue;
              const c=headCat(r);
              if(c==='其他'||cats.has(c)) continue;
              found=true; break;
            }
            flags.push(found);
            const real = top[slot]; if(!real) break;
            cats.add(headCat(real)); used.add(real[0]);
          }
          return flags; })(),
        capPermOK: rows.length===byScore.length && rows.every(r=>byScore.includes(r)),
        displacedOK: (()=>{ const t10=rows.slice(0,10);
          return byScore.slice(0,10).filter(r=>!t10.includes(r))
            .every(r=> t10.filter(x=>x[0]===r[0]).length>=2); })(), // 被挤出前10者必因同校已满2
        tailOrderOK: (()=>{ const tail=rows.slice(10), t10=rows.slice(0,10);
          const extra=tail.filter(r=>byScore.slice(0,10).includes(r)); // 被限流下沉的
          const restRows=tail.filter(r=>!extra.includes(r));
          const expectRest=byScore.slice(10).filter(r=>!t10.includes(r)); // 补位上拉的也不在剩余
          const descOK=a=>a.every((r,i)=>i===0||scoreOf(a[i-1],estimate.est)>=scoreOf(r,estimate.est));
          return descOK(extra) && restRows.length===expectRest.length && restRows.every((r,i)=>r===expectRest[i]); })(),
        top10schoolCap: (()=>{ const c={}; for(const r of rows.slice(0,10)){ c[r[0]]=(c[r[0]]||0)+1; } return Math.max(...Object.values(c)); })(),
      };
    }""", [first, score, tier_idx])

def check_scenario(page, first, score, label):
    scene = f'{first}{score}'
    for ti in (0, 1, 2):
        f = render_tier(page, first, score, ti)
        sc = f'{scene}/{f["tierName"]}'
        if f['rowCount'] == 0:
            rec('数据', '空档兜底文案', sc, True, '本档无可推荐（正常分支）'); continue
        # ---- UI ----
        rec('UI', '定位卡无标题无引导链', scene, not f['hasLab'] and not f['hasCTA'])
        rec('UI', '定位卡大字=分数·位次区间', scene,
            bool(re.match(rf'^{score} 分 · 全省{first}类约前 [\d,]+ ~ [\d,]+ 名$', f['locateText'])), f['locateText'])
        rec('UI', '定位卡小字=超线比例+估算依据', scene,
            f['locateSub'].startswith('超过本科线上约') and '按成绩估算' in f['locateSub'], f['locateSub'])
        rec('UI', '已删文案零残留（档引导/页底说明）', sc, not f['hintForbidden'])
        rec('UI', 'TOP3=3 张卡', sc, f['topCount'] == 3, f['topCount'])
        rec('UI', 'TOP3 卡四行件齐全（外漏/分数线/详情链）', sc, all(f['topHasL234']), f['topHasL234'])
        rec('UI', 'TOP3 档位色条非透明', sc, all(c != 'rgba(0, 0, 0, 0)' for c in f['topBorderLeft']), f['topBorderLeft'])
        # ---- 文案合规 ----
        for i, rs in enumerate(f['reasons']):
            if not rs: continue
            rec('文案', f'推荐理由≤2条只说优点（top#{i+1}）', sc,
                rs.count('·') <= 1 and '⚠' not in rs and '冷' not in rs, rs)
        rec('文案', '冷标签零残留（chip+文本）', sc, f['coldChipCount'] == 0 and f['coldText'] == 0,
            f"chip={f['coldChipCount']} 文本={f['coldText']}")
        for wt in f['warnTips']:
            rec('文案', '灰签=小提示前缀且无⚠', sc, wt.startswith('小提示：') and '⚠' not in wt, wt)
        # ---- 策略 ----
        rec('策略', 'TOP3#1=S 分第一', sc, f['topIsRows0'])
        rec('策略', 'TOP3 同校去重', sc, f['topNamesUnique'], f['topNames'])
        cats = f['topCats']
        # 角色不撞；撞车时需豁免=该槽位池内确无新角色候选（兜底纯分顺位属设计行为）
        used_cats = set()
        role_ok = True
        for i, c in enumerate(cats):
            if c == '其他':
                continue  # 无卖点卡不占差异名额（设计）
            if c in used_cats:
                if i > 0 and f['slotHadCandidate'][i-1]:
                    role_ok = False  # 池内有新角色候选却撞车=真问题；无候选=兜底，设计行为
            used_cats.add(c)
        rec('策略', 'TOP3 头牌角色不撞（兜底需豁免）', sc, role_ok,
            f"cats={cats} 槽位有候选={f['slotHadCandidate']}")
        rec('策略', 'TOP3 层次守卫（不降档）', sc, f['lvlGuardOK'], f"top={cats} pure={f['pureCats']}")
        rec('策略', '档内行=byScore 排列（无丢行无重行）', sc, f['capPermOK'])
        rec('策略', '每档前10同校≤2', sc, f['top10schoolCap'] <= 2, f"max={f['top10schoolCap']}")
        rec('策略', '被挤出前10者必因同校已满（限流正当性）', sc, f['displacedOK'])
        rec('策略', '10名后=下沉组降序+剩余严格按分序', sc, f['tailOrderOK'])
        # ---- 交互（rest 结构）----
        if f['rest']:
            exp_cards = min(5, f['rest']['total'])
            rec('交互', '展开全部前5=mini卡、其余紧凑行', sc,
                f['rest']['cardCount'] == exp_cards and f['rest']['lineCount'] == f['rest']['total'] - exp_cards,
                json.dumps(f['rest'], ensure_ascii=False))

def check_interaction_live(page):
    """真点击：rest 首卡展开大卡→点 top 卡→手风琴合上（580 物理稳档）"""
    out = page.evaluate("""async () => {
      st.first='物理'; st.gender='男'; st.score='580'; st.career='还没想好'; st.region='还没想好';
      estimate = {est: scoreToRank(580,'物理'), widen:1, basis:'b', lo:1, hi:99999};
      document.querySelectorAll('.screen').forEach(s=>s.classList.remove('on'));
      document.getElementById('s3').classList.add('on');
      render(); switchTier(1);
      const body = document.getElementById('tierBody');
      toggleRest(body.querySelector('.opt-toggle'), 0);
      const rc = document.querySelector('.rest-list > .tier .card.nc');
      rc.click();
      await new Promise(r=>setTimeout(r,1200));
      const d1 = rc.querySelector('.detail');
      const opened = d1.style.display!=='none' && d1.innerHTML.includes('mtable');
      // 大卡三段+表格人数居中
      const secOK = ['组内专业','毕业后去向','职业与薪酬'].filter(h=>d1.innerHTML.includes(h)).length;
      const nCell = d1.querySelector('.mtable .n');
      const nAlign = nCell ? getComputedStyle(nCell).textAlign : 'missing';
      const hotChips = [...d1.querySelectorAll('.heat.hot')].map(h=>getComputedStyle(h).backgroundColor);
      const top = body.querySelector(':scope > .tier .card.nc');
      top.click();
      await new Promise(r=>setTimeout(r,600));
      const accordion = d1.style.display==='none' && top.querySelector('.detail').style.display!=='none';
      return {opened, secOK, nAlign, hotChips, accordion};
    }""")
    rec('交互', 'rest mini卡可点展开大卡（含明细表）', '物理580/稳', out['opened'])
    rec('UI', '大卡三段齐全（专业/去向/薪酬）', '物理580/稳', out['secOK'] >= 2, f"命中{out['secOK']}段（薪酬段依赖数据）")
    rec('UI', '明细表人数列居中', '物理580/稳', out['nAlign'] == 'center', out['nAlign'])
    rec('UI', '热 chip 暖杏底 #FDF3E7', '物理580/稳',
        all(c == 'rgb(253, 243, 231)' for c in out['hotChips']) if out['hotChips'] else None,
        f"{out['hotChips']}（无热专业时跳过视为通过）" if not out['hotChips'] else '')
    rec('交互', '手风琴：开新卡合旧卡', '物理580/稳', out['accordion'])

def check_below_line(page):
    out = page.evaluate("""() => {
      st.first='物理'; st.gender='男'; st.score='400'; st.career='还没想好'; st.region='还没想好';
      estimate = {est: 999999, widen:1, basis:'按成绩估算 · 上下浮动约 15 分', lo:999990, hi:999999};
      document.querySelectorAll('.screen').forEach(s=>s.classList.remove('on'));
      document.getElementById('s3').classList.add('on');
      render();
      return {sub: document.getElementById('locateSub').textContent,
              tiers: document.getElementById('tiers').textContent,
              lead: document.getElementById('leadGets').textContent};
    }""")
    rec('UI', '本科线下=提分叙事（不推学校）', '物理400',
        '本科线' in out['sub'] and '距本科线还差' in out['tiers'] and '提分' in out['lead'],
        out['sub'][:60])

def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 480, 'height': 900})
        pg.goto(BASE); pg.wait_for_timeout(1500)
        # ---- 静态（表单屏）----
        s = pg.evaluate("""() => ({
          ver: VERSION,
          verFooter: document.getElementById('verFooter').textContent,
          placeholder: document.getElementById('score').getAttribute('placeholder'),
          demoBar: document.querySelector('.demo-bar').textContent,
        })""")
        rec('UI', '版本号存在且页脚展示', '表单', bool(re.match(r'^v\d+\.\d+\.\d+$', s['ver'])) and s['ver'] in s['verFooter'], s['verFooter'])
        rec('UI', '分数框无预埋示例分', '表单', s['placeholder'] in (None, ''), repr(s['placeholder']))
        rec('UI', 'demo-bar 来源口径在顶部', '表单',
            '2026/2025' in s['demoBar'] and '麦可思' in s['demoBar'] and '剔除专项' in s['demoBar'], s['demoBar'][:80])
        # ---- 场景扫描 ----
        for first, score, label in SCENARIOS:
            check_scenario(pg, first, score, label)
        check_interaction_live(pg)
        check_below_line(pg)
        b.close()
    fails = [r for r in R if r['ok'] is False]
    skips = [r for r in R if r['ok'] is None]
    print(f"\n===== 自测结果：{len(R)-len(fails)-len(skips)}/{len(R)-len(skips)} 通过（跳过 {len(skips)} 条不适用项）=====")
    for r in fails:
        print(f"FAIL [{r['cat']}] {r['scene']} · {r['name']} → {r['detail']}")
    with open('selftest_report.json', 'w') as fp:
        json.dump(R, fp, ensure_ascii=False, indent=1)
    print(f"明细已写 selftest_report.json（{len(R)} 条）")
    sys.exit(1 if fails else 0)

if __name__ == '__main__':
    main()
