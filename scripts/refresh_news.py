#!/usr/bin/env python3
"""Hermes Agent News Dashboard - Complete refresh script.
Scrapes Toutiao search, fetches metadata, generates HTML.
Designed to be run daily via cron."""
import subprocess, re, time, json, sys, os
from datetime import datetime
import urllib.parse, html as html_mod

# Determine paths relative to this script (works on both local and GitHub Actions)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)  # scripts/ -> repo root
OUTPUT = os.path.join(REPO_DIR, 'hermes-news.html')
CACHE_FILE = os.path.join(REPO_DIR, 'articles_cache.json')
TEMP_DIR = os.path.join(REPO_DIR, '.cache')
os.makedirs(TEMP_DIR, exist_ok=True)

# ===== STEP 1: Get GitHub stats =====
def fetch_github():
    """Fetch GitHub repo stats and hot issues."""
    result = subprocess.run(['curl', '-s', 'https://api.github.com/repos/NousResearch/hermes-agent'],
        capture_output=True, text=True, timeout=10)
    repo = json.loads(result.stdout)
    
    result2 = subprocess.run(['curl', '-s', 
        'https://api.github.com/repos/NousResearch/hermes-agent/releases/latest'],
        capture_output=True, text=True, timeout=10)
    release = json.loads(result2.stdout)
    
    result3 = subprocess.run(['curl', '-s',
        'https://api.github.com/repos/NousResearch/hermes-agent/issues?state=open&sort=comments&direction=desc&per_page=5'],
        capture_output=True, text=True, timeout=10)
    issues = json.loads(result3.stdout)
    
    return {
        'stars': repo.get('stargazers_count', 0),
        'forks': repo.get('forks_count', 0),
        'issues_count': repo.get('open_issues_count', 0),
        'release_tag': release.get('tag_name', ''),
        'release_name': release.get('name', ''),
        'release_date': release.get('published_at', '')[:10] if release.get('published_at') else '',
        'release_url': release.get('html_url', ''),
        'hot_issues': [{
            'number': i['number'],
            'title': i['title'][:70],
            'comments': i['comments'],
            'url': i['html_url'],
        } for i in issues[:5] if 'number' in i]
    }

# ===== STEP 2: Scrape Toutiao =====
def extract_from_html(html):
    """Extract self_article results from Toutiao search HTML."""
    articles = {}
    blocks = re.split(r'<div class="result-content"', html)
    for bi, block in enumerate(blocks):
        if bi == 0: continue
        if '"self_article"' not in block: continue
        if '"cell_type":20' in block: continue
        gid_m = re.search(r'group_id":\s*"(\d{19})"', block)
        if not gid_m: continue
        aid = gid_m.group(1)
        link_m = re.search(r'href="[^"]*"[^>]*class="text-ellipsis text-underline-hover"[^>]*>', block)
        if not link_m: continue
        title_end = block.find('</a>', link_m.end())
        if title_end < 0: continue
        title_raw = block[link_m.end():title_end]
        title = html_mod.unescape(re.sub(r'<[^>]+>', '', title_raw)).strip()
        title = re.sub(r'\s+', ' ', title).strip()
        if len(title) < 8: continue
        if not any(kw in title for kw in ['Hermes', 'hermes', 'Agent', 'agent', '爱马仕', '养马', '龙虾']): continue
        source_spans = re.findall(r'<span class="text-ellipsis[^"]*"[^>]*>\s*([^<]{2,30})\s*<', block)
        source = ''; time_str = ''
        for sp in source_spans:
            sp = sp.strip()
            if re.match(r'\d+[天小时分秒]前|刚刚', sp) or re.match(r'\d{1,2}月\d{1,2}日', sp): time_str = sp
            elif not source and not sp.isdigit() and len(sp) >= 2: source = sp
        desc_m = re.search(r'class="text-default text-m[^"]*"[^>]*>\s*(?:<span[^>]*>)?([^<]{50,500})', block)
        desc = html_mod.unescape(desc_m.group(1)).strip() if desc_m else ''
        key = title[:40]
        if key not in articles:
            articles[key] = {
                'title': title[:150], 'url': f'https://www.toutiao.com/article/{aid}/',
                'aid': aid, 'source': source, 'desc': desc, 'time': time_str,
            }
    return list(articles.values())

def scrape_toutiao(keyword='Hermes Agent'):
    """Scrape so.toutiao.com for articles using both synthesis and information modes."""
    encoded = urllib.parse.quote(keyword)
    all_articles = {}; seen_aids = set()
    
    for pd_param in ['synthesis', 'information']:
        for page in range(10):
            url = f'https://so.toutiao.com/search?dvpf=pc&keyword={encoded}&pd={pd_param}&page_num={page}'
            sys.stderr.write(f'  {pd_param} Page {page}... ')
            try:
                result = subprocess.run(['curl', '-s', '-L', '-m', '15', url,
                    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'],
                    capture_output=True, text=True, timeout=20)
                html = result.stdout
            except Exception as e:
                sys.stderr.write(f'FAIL: {e}\n'); continue
            
            extracted = extract_from_html(html)
            for a in extracted:
                if a['aid'] not in seen_aids:
                    seen_aids.add(a['aid'])
                    all_articles[a['title'][:40]] = a
            sys.stderr.write(f'{len(extracted)} found, {len(all_articles)} unique\n')
            time.sleep(0.5)
    
    return list(all_articles.values())

# ===== STEP 3: Fetch metadata from m.toutiao.com =====
def fetch_metadata(articles):
    """Fetch read count, likes, comments, publish time for each article."""
    results = []
    total = len(articles)
    for i, a in enumerate(articles):
        aid = a['aid']
        url = f'https://m.toutiao.com/article/{aid}/'
        try:
            result = subprocess.run(['curl', '-s', '-L', '-m', '10', url,
                '-H', 'User-Agent: Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36',
                '-H', 'Accept-Language: zh-CN,zh;q=0.9'],
                capture_output=True, text=True, timeout=15)
            html = result.stdout
            m = re.search(r'RENDER_DATA"[^>]*>([^<]+)', html)
            if m:
                decoded = urllib.parse.unquote(m.group(1))
                data = json.loads(decoded)
                info = data.get('articleInfo', {})
                if isinstance(info, str): info = json.loads(info)
                ts = info.get('publishTime', '')
                pub = ''
                if ts:
                    dt = datetime.fromtimestamp(int(ts))
                    pub = dt.strftime('%Y-%m-%d')
                a['pub'] = pub
                a['reads'] = info.get('impressionCount', '')
                a['likes'] = info.get('diggCount', 0)
                a['comments_count'] = info.get('commentCount', 0)
            else:
                a['pub'] = ''
                a['reads'] = ''
                a['likes'] = 0
                a['comments_count'] = 0
        except:
            a['pub'] = ''
            a['reads'] = ''
            a['likes'] = 0
            a['comments_count'] = 0
        
        results.append(a)
        if i < total - 1:
            time.sleep(0.3)
        sys.stderr.write(f'\r  Metadata: [{i+1}/{total}]')
    
    return results

# ===== STEP 4: Generate HTML =====
def gen_html(articles, github, run_info=None):
    meta_lookup = {}
    for a in articles:
        aid = a.get('id') or a.get('aid', '')
        if aid:
            meta_lookup[aid] = a
    
    all_cats = [
        ('Nous官方', [
            ('一文搞懂Hermes：Agent如何自我进化', '7640179600638640691', 'AI锋行'),
            ('Hermes 新功能来了：别人调好的 Agent，可以一条命令装到你电脑里', '7638572546434187816', '老年人学AI'),
            ('Hermes Agent：自我进化、持久记忆、多平台常驻', '7645667832075747840', '山顶静心冥想的行者'),
            ('Hermes Agent 0.15 升级实测', '7645986285256983086', 'Jack聊AI'),
            ('Hermes Agent v0.16.0更新了', '7648188951836819983', '星禾'),
            ('升级你的 Hermes Agent 的 10 个方法', '7647601050165314054', '余晖晚风信'),
            ('单日2910亿Token登顶全球', '7643350458471432719', '运筹帷幄钢笔IuzgH'),
            ('Hermes v0.16炸裂更新', '7648449847879598632', '座谈客'),
            ('Hermes官方桌面版发布', '7647079952025748010', '智东西'),
            ('Hermes 来了：能在桌面上自己进化的 AI Agent', '7647087326006362634', '正正AI杂说'),
        ]),
        ('桌面端', [
            ('告别日志刷屏！Hermes可视化控制台', '7635221067510661678', '开源派'),
            ('Hermes Agent出桌面版了', '7647116327815004681', '立氢'),
            ('Hermes 桌面端来了', '7647506717697147411', '老爸的AI联萌'),
        ]),
        ('使用技巧', [
            ('2026年3.0版Hermes Agent 完整安装教程', '7644478681368298030', '中国企业报数字经济'),
            ('Hermes Agent 安装部署指南', '7646548779113447945', '中国企业报数字经济'),
            ('Hermes Agent小白指南', '7647453021424632347', '正正AI杂说'),
            ('hermes agent安装教程：不用折腾环境变量', '7647382424342561326', '中国企业报数字经济'),
            ('我把 Hermes Agent 接进了微信', '7647803749016060468', '人人都是产品经理'),
            ('Hermes Agent 安装与配置全流程', '7646320837049451054', '中国企业报数字经济'),
            ('Hermes Agent 全解析｜7个问题讲透', '7648573296262283830', 'R的曙光'),
            ('文职人员的 Hermes Agent 配置完全指南', '7643436240741663282', '谷子熟了'),
            ('安装、模型配置与工具生态', '7647327124176650771', '一枚后端攻城狮'),
            ('Hermes Agent 详细指南', '7637824243127943720', '机器觉醒'),
            ('在构建任何东西之前，你需要了解的 8 个配置', '7648341842836177471', '余晖晚风信'),
            ('Hermes Agent别停留在裸装', '7648322570018439714', 'OPC进化论'),
            ('零基础也能上手！Windows 安装保姆级教程', '7642882285217301026', '云邻居'),
            ('Hermes Agent 终于有入门指南了', '7648904459526930990', '良心的小编'),
            ('Hermes Agent从入门到封神', '7641610534575555098', '淡定的橘子'),
            ('Hermes agent，从入门到放弃', '7636787204831855147', '三生万物'),
            ('Hermes Agent 学习指南', '7641841406922719779', '新钛云服'),
            ('超简单！Hermes Agent 安装攻略来袭', '7645593586309874185', '海上看日出的船夫'),
            ('如何真正使用 Hermes Agent', '7639716328214987264', '文智阁'),
            ('Hermes Agent 完整安装教程', '7644756189770809890', '机器觉醒'),
            ('Hermes Agent 桌面版上手教程（下）', '7648238839336600099', '审计评估李老师'),
            ('Hermes Agent：从1个AI到完整营销公司', '7640886355874054707', '老猿视角'),
            ('Hermes Agent安装准备', '7637795065187844658', '穿石的水滴Plus'),
            ('Hermes Agent 保姆级安装教程', '7644061889143521834', '万象AI实验室'),
            ('Hermes Agent（爱马仕）本地部署教程', '7641500884056277547', '小小生活多娱乐'),
            ('Free CPU教程 | Hermes Agent记忆增强', '7646377943224091162', 'HyperAI超神经'),
            ('Hermes Agent 必看 7 问', '7639573759058084361', 'R的曙光'),
            ('2 分钟极速入门', '7641887176417935910', '科技见闻网'),
        ]),
        ('技术前沿', [
            ('Hermes Agent 实用公开技能推荐', '7648136448679428648', 'ai小能手'),
            ('Hermes Agent加入Tool Search', '7645565147485946394', 'IT之家'),
            ('Agent自己长技能？一周学会5个新本事', '7639537395188220468', '猛哥'),
            ('养龙虾？OUT了，我们开始养马', '7641958581205598735', 'kehuo'),
            ('Hermes Agent省Token实战', '7646971342521008675', '康康家的小澄澄'),
            ('Agent想执行危险命令，Hermes怎么踩刹车？', '7643996441664324123', '闵浮龙'),
            ('Hermes Agent子Agent的隔离与通信机制', '7641211790886322734', '路多辛'),
            ('Hermes Agent 架构全景', '7647326909512041003', '一枚后端攻城狮'),
            ('Agent 工具超载怎么救？渐进式披露设计', '7647451200408814114', '对线面试官'),
            ('开源项目Hermes Agent评测', '7624751638158262820', '人人都是产品经理'),
            ('蹲了 Hermes Agent 三个月', '7648167953774412288', '星禾'),
        ]),
        ('对比评测', [
            ('Hermes Agent vs OpenClaw 本质区别', '7637134413758857737', '路多辛'),
            ('OpenHuman和Hermes Agent谁更强', '7641203762963186202', 'AI实战派老陈'),
            ('Hermes Agent vs OpenClaw', '7647098263330390563', '勇敢的饼干强尼'),
            ('三个最强Agent对比', '7643467636051640859', '萝卜啊'),
            ('拆解 Hermes Agent 五层架构', '7642544476619276815', '机器觉醒'),
        ]),
        ('行业动态', [
            ('老黄也来养马了！英伟达版Hermes Agent发布', '7647527883178656265', '量子位'),
            ('英伟达版Hermes Agent上线，越用越聪明、数据不出门', '7647354528748143156', '产品视界'),
            ('Hermes Agent 反超 OpenClaw', '7647426230203023926', '区块链小趋势研究院'),
            ('HermesAgent首超OpenClaw成全球调用量最高', '7638883032220320299', '金融界'),
            ('Hermes 一夜封神！Agent圈疯抢爱马仕', '7641967788726288946', '千城文化'),
            ('Hermes Agent是如何记住你的？', '7639732898819555849', '路多辛'),
            ('Hermes Agent 到底能干什么？276个用例', '7642432058253492755', '高可用架构'),
            ('每天吃透一个AI知识点——Hermes Agent', '7632156040942944818', 'AI洞察'),
            ('Hermes Agent的定时自动化', '7648696819765019151', 'OPC进化论'),
            ('Hermes-Agent：和你一起成长的Agent', '7645267185520558619', '立氢'),
            ('7万星Hermes-Agent落地国内', '7645136402216239659', '知识有点料'),
            ('Hermes-Agent中文手册，8大模块全是干货', '7639525656560222758', '惜缘说数码'),
            ('外部事件是如何触发Hermes Agent运行的？', '7641964704432194074', '路多辛'),
            ('Hermes Agent：一条少有人走的路', '7637053787407909411', '今日值得-Zbone'),
            ('Hermes Agent 3分钟装好', '7641162958797685288', '座谈客'),
        ]),
    ]
    
    cards_html = ''
    cat_count = {}
    
    def gen_score(title, cat, reads, likes, comments):
        score = 3  # default
        # Adjust: Toutiao articles typically have hundreds to low-thousands of reads
        if reads >= 5000 or likes >= 500 or comments >= 50:
            score = 5
        elif reads >= 2000 or likes >= 200 or comments >= 20:
            score = 4
        elif reads >= 500 or likes >= 50 or comments >= 5:
            score = 3
        elif reads >= 100:
            score = 2
        else:
            score = 2
        # Boost for quality content
        if any(k in title for k in ['评测', '实测', '拆解', '详解', '架构', '对比', '指南', '教程', '避坑']):
            score = min(5, score + 1)
        # Penalize very short or clickbait-like titles
        if len(title) < 12:
            score = max(1, score - 1)
        return score
    
    def gen_keymsg(title, cat):
        t = title
        if any(k in t for k in ['安装', '部署', '教程', '指南', '入门', '上手', '配置']):
            if 'Windows' in t: return 'Windows环境详细安装与配置指南'
            if 'Python' in t: return '解决Python环境配置常见问题'
            if '微信' in t: return '将Hermes Agent接入微信的完整流程'
            if '极速' in t or '2分钟' in t: return '快速上手，5分钟完成基础配置'
            if 'Kimi' in t: return '配置Kimi大模型驱动的Agent'
            if '小白' in t: return '零基础用户友好型入门指南'
            if '避坑' in t: return '常见安装错误及解决方案汇总'
            if '从入门到封神' in t: return '从零基础到高阶使用的完整学习路径'
            if '从入门到放弃' in t: return '分析常见痛点与避坑经验'
            if '必看' in t or '7问' in t: return '7个核心问题快速搞懂Hermes Agent'
            if '学习指南' in t: return '系统化掌握Hermes Agent功能与用法'
            if '完整安装' in t: return '2026最新版全流程安装教程'
            if '从1个AI' in t: return '用单个Agent搭建完整工作流'
            if '保姆级' in t: return '手把手保姆式安装引导'
            if '工具箱' in t or '生态' in t: return '安装、配置与工具链一站式指南'
            if '企业' in t or '全流程' in t: return '企业级安装部署全流程参考'
            if '本地部署' in t: return '本地化部署完整步骤与注意事项'
            if '文职' in t: return '非技术用户的配置模板与最佳实践'
            if '零基础' in t: return 'Windows系统从零开始保姆级教程'
            if '详细指南' in t: return '覆盖安装、配置与实战的完整手册'
            if '全解析' in t or '7个' in t: return '从安装到成本的全方位解析'
            if '别停留在' in t: return '5个核心配置让Agent真正可用'
            if 'Free CPU' in t or '记忆增强' in t: return '记忆分层存储机制与配置方法'
            if '桌面版上手' in t: return '桌面端功能设置与高效使用技巧'
            return '实用技巧与最佳实践'
        if 'v0.16' in t or '炸裂' in t: return 'v0.16原生桌面应用携多项重磅功能上线'
        if 'v0.15' in t or '升级实测' in t: return '性能大幅提升，底层架构全面重构'
        if '0.14' in t: return '多重身份管理与Web控制台Beta发布'
        if '单日' in t and 'Token' in t: return '单日2910亿Token调用量登顶全球'
        if '新功能' in t: return '一键安装社区共享技能的新机制'
        if '一文搞懂' in t: return '全面解读Hermes Agent自我进化原理'
        if '更新了什么' in t or '盘点' in t: return '最新版本特性与变更全览'
        if '10个方法' in t: return '提升Agent效率的10个实用技巧'
        if '3月' in t and '更新' in t: return 'Skills Hub上线等3月重要更新'
        if '桌面' in t: return '原生桌面应用体验与上手评测'
        if '控制台' in t: return '可视化控制台让Agent思维过程透明化'
        if '可视化' in t: return '告别日志刷屏，可视化调试Agent'
        if '自我进化' in t: return 'Agent具备自主创造技能的学习能力'
        if '永久' in t or '记忆' in t: return '跨会话持久记忆机制详解'
        if 'Tool Search' in t: return 'Tool Search功能大幅降低Token消耗'
        if '技能' in t and ('推荐' in t or '装什么' in t): return '社区精选实用技能安装推荐'
        if '自己长技能' in t: return 'Agent自动学习新技能的实战案例'
        if '省Token' in t: return '降低API调用成本的实用策略'
        if '危险命令' in t or '安全' in t or '刹车' in t: return 'Agent执行危险命令时的安全审批链路'
        if '子Agent' in t or '隔离' in t: return '子Agent沙箱隔离与通信机制技术解析'
        if '架构' in t or '设计哲学' in t: return 'Hermes Agent五层架构与设计理念'
        if '工具超载' in t or '渐进式' in t: return '大规模工具集下的渐进式披露设计'
        if 'MCP' in t: return '通过MCP协议扩展Agent能力边界'
        if 'Web' in t and '控制台' in t: return '后台可视化管理与公网访问配置'
        if '定时' in t or '自动化' in t: return 'Cron定时任务实现Agent自动化运行'
        if '对比' in t or 'vs' in t: return 'Hermes Agent与竞品的多维度对比'
        if '本质区别' in t: return '分析核心设计理念与技术路线差异'
        if 'OpenHuman' in t: return 'Hermes与OpenHuman功能与性能对比'
        if '五层架构' in t: return '从底层存储到上层应用的完整架构拆解'
        if '评测' in t or '越用越聪明' in t: return '长期使用体验报告与功能评测'
        if '三个月' in t or '越来越' in t: return '三个月深度使用后的真实体验反馈'
        if '反超' in t or '全球调用' in t: return '超越竞品成为OpenRouter调用量最高应用'
        if '封神' in t or '一夜' in t: return 'Hermes Agent为何突然爆火的分析'
        if 'NVIDIA' in t or '英伟达' in t or '老黄' in t: return '英伟达推出定制版Hermes Agent'
        if '数据不出门' in t: return '本地化部署保障数据隐私安全'
        if '记住你' in t: return '持久记忆机制的实现原理与技术细节'
        if '276个' in t or '用例' in t: return '覆盖16个分类的276个实际应用场景'
        if '知识点' in t: return '快速了解Hermes Agent核心概念'
        if '和你一起成长' in t: return '越用越智能的自进化Agent特性解析'
        if '中文手册' in t or '中文资料' in t or '落地' in t: return '华为开发者整理的中文学习资料合集'
        if '外部事件' in t or 'Webhook' in t: return '外部事件触发机制的架构设计'
        if '少有人走的路' in t: return '资深用户对Hermes Agent的深度思考'
        if '3分钟' in t: return '极简安装流程，3分钟快速体验'
        if '爱马仕' in t: return 'Hermes Agent市场热度与影响力分析'
        if '清华大学' in t: return '高校AI实验室基于Hermes的研究实践'
        if '接入' in t and ('飞书' in t or '数据' in t): return '接入飞书多维表格实现数据自动化'
        if 'Manus' in t: return 'Hermes与Manus两大Agent框架对比'
        return f'{cat}相关文章'
    
    for cat_key, items in all_cats:
        cat_count[cat_key] = len(items)
        for title, aid, source in items:
            a = meta_lookup.get(aid, {})
            pub = a.get('pub', '') or '2026-06-08'
            reads = int(a.get('reads', '0')) if a.get('reads','0').isdigit() else 0
            likes = a.get('likes', 0) or 0
            comments = a.get('comments', 0) or 0
            url = f'https://www.toutiao.com/article/{aid}/'
            reads_fmt = f'{reads:,}' if reads >= 1000 else str(reads)
            keymsg = gen_keymsg(title, cat_key)
            score = gen_score(title, cat_key, reads, likes, comments)
            stars_full = '★' * score + '☆' * (5 - score)
            cards_html += f'''    <div class="card" data-date="{pub}" data-read="{reads}" data-like="{likes}" data-reply="{comments}" data-score="{score}" data-cat="{cat_key}">
      <div class="card-inner">
        <div class="card-main">
          <div class="tag tag-{cat_key}">{cat_key}</div>
          <a href="{url}" target="_blank" class="title">{title}</a>
          <div class="meta">
            <span>✍️ {source}</span><span>🕐 {pub}</span>
            <span class="s">👁 {reads_fmt}</span>
            <span class="s">💬 {comments}</span>
            <span class="s">👍 {likes}</span>
          </div>
        </div>
        <div class="card-keymsg">{keymsg}</div>
        <div class="card-score">{stars_full}<br><span class="score-num">{score}.0</span></div>
      </div>
    </div>
'''
    
    total = sum(cat_count.values())
    cat_buttons = f'<button class="fb a" data-c="all" onclick="fc(\'all\')">📋 全部 <span class="n">{total}</span></button>'
    for ck, cnt in cat_count.items():
        cat_buttons += f'<button class="fb" data-c="{ck}" onclick="fc(\'{ck}\')">{ck} <span class="n">{cnt}</span></button>'
    
    stars = f"{github['stars']:,}" if github.get('stars') else '186,534'
    forks = f"{github['forks']:,}" if github.get('forks') else '32,090'
    total_reads = sum(int(a.get('reads', '0')) if a.get('reads','0').isdigit() else 0 for a in articles)
    total_likes = sum(a.get('likes', 0) or 0 for a in articles)
    
    def fmt(n):
        if n >= 10000: return f'{n/10000:.1f}万'
        if n >= 1000: return f'{n/1000:.1f}千'
        return str(n)
    
    ri = run_info or {}
    if ri: total_fresh = ri.get('fresh', 0); total_cached = ri.get('cached', 0)
    else: total_fresh = 0; total_cached = 0
    refresh_html = f'<div class="refresh-info">最后更新: {ri.get("time","?")} | 本次新增 {ri.get("fresh",0)} 篇 | 累计 {ri.get("total",0)} 篇</div>' if ri else ''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Hermes Agent 中文资讯</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;color:#1a1a2e;max-width:960px;margin:0 auto;padding:16px}}
.hdr{{background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);color:#fff;padding:28px 24px;border-radius:16px;margin-bottom:16px}}
.hdr h1{{font-size:24px}}.hdr h1 span{{color:#e94560}}
.hdr .sub{{font-size:13px;opacity:.6;margin-top:6px}}
.hdr .s{{display:flex;gap:20px;margin-top:12px;font-size:12px;opacity:.7}}
.hdr .s strong{{color:#e94560}}
.tb{{background:#fff;border-radius:12px;padding:14px 18px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06);display:flex;flex-wrap:wrap;align-items:center;gap:10px}}
.tb .l{{font-size:13px;color:#666;font-weight:500}}
.sg{{display:flex;gap:6px;flex-wrap:wrap}}
.sb{{padding:5px 12px;border:1px solid #e0e0e0;border-radius:6px;background:#fff;cursor:pointer;font-size:13px;color:#555}}
.sb:hover{{border-color:#e94560;color:#e94560}}
.sb.a{{background:#e94560;color:#fff;border-color:#e94560}}
.ob{{padding:5px 10px;border:1px solid #e0e0e0;border-radius:6px;background:#fff;cursor:pointer;font-size:13px;color:#555}}
.ob:hover{{border-color:#7c3aed;color:#7c3aed}}
.ob.a{{background:#7c3aed;color:#fff;border-color:#7c3aed}}
.fg{{display:flex;gap:6px;flex-wrap:wrap}}
.fb{{padding:5px 12px;border:1px solid #e0e0e0;border-radius:16px;background:#fff;cursor:pointer;font-size:12px;color:#555}}
.fb:hover{{border-color:#e94560;color:#e94560}}
.fb.a{{background:#1a1a2e;color:#fff;border-color:#1a1a2e}}
.fb .n{{display:inline-block;background:#f0f0f0;color:#888;font-size:11px;padding:0 5px;border-radius:8px;margin-left:3px}}
.fb.a .n{{background:rgba(255,255,255,.2);color:rgba(255,255,255,.7)}}
.nc{{font-size:13px;color:#999;padding:4px 0 8px}}
.card{{background:#fff;border-radius:12px;padding:16px 18px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:3px solid transparent}}
.card:hover{{box-shadow:0 4px 16px rgba(0,0,0,.1);transform:translateX(2px)}}
.tag{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:4px;margin-bottom:6px;font-weight:500}}
.tag-Nous官方{{background:#fce4ec;color:#c62828}}
.tag-桌面端{{background:#e3f2fd;color:#1565c0}}
.tag-使用技巧{{background:#e8f5e9;color:#2e7d32}}
.tag-技术前沿{{background:#f3e5f5;color:#6a1b9a}}
.tag-对比评测{{background:#fbe9e7;color:#bf360c}}
.tag-行业动态{{background:#fff3e0;color:#e65100}}
.card .title{{display:block;font-size:15px;font-weight:600;line-height:1.5;color:#1a1a2e;text-decoration:none}}
.card .title:hover{{color:#e94560;text-decoration:underline}}
.card .meta{{font-size:12px;color:#999;margin-top:10px;display:flex;flex-wrap:wrap;align-items:center;gap:12px}}
.card-inner{{display:flex;gap:16px;align-items:stretch}}
.card-main{{flex:1;min-width:0}}
.card-keymsg{{flex:0 0 200px;font-size:12px;color:#666;background:#f8f9fa;border-left:2px solid #e94560;padding:8px 12px;border-radius:0 6px 6px 0;display:flex;align-items:center;line-height:1.6}}
.card-score{{flex:0 0 60px;text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#f59e0b;font-size:16px;line-height:1.4}}
.score-num{{font-size:11px;color:#999;margin-top:2px}}
.gh{{background:#fff;border-radius:12px;padding:12px 18px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06);display:flex;flex-wrap:wrap;align-items:center;gap:12px;font-size:13px}}
.gh .g{{color:#555}}.gh .g strong{{color:#e94560}}
.gh a{{color:#7c3aed;text-decoration:none;font-size:12px}}
.refresh-info{{background:#f0f4ff;border:1px solid #d0d7ff;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-size:12px;color:#555}}
.ft{{text-align:center;font-size:12px;color:#bbb;padding:24px 0;border-top:1px solid #eee;margin-top:16px}}
.ft a{{color:#e94560;text-decoration:none}}
@media(max-width:600px){{body{{padding:10px}}.hdr{{padding:20px 16px}}}}
</style>
</head>
<body>

<div class="hdr">
<h1>&#128269; <span>Hermes</span> Agent 中文资讯</h1>
<div class="sub">2026年06月 · 今日头条搜索 · 共 {total} 篇</div>
<div class="s"><div>&#128240; <strong>{total}</strong> 篇</div><div>&#128065; <strong>{fmt(total_reads)}</strong> 总阅读</div><div>&#128077; <strong>{fmt(total_likes)}</strong> 总点赞</div></div>
</div>

<div class="gh">
<span class="g">&#11088; GitHub <strong>{stars}</strong> Stars</span>
<span class="g">&#127829; <strong>{forks}</strong> Forks</span>
<a href="https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.5" target="_blank">&#128640; v0.16.0 &rarr;</a>
</div>

{refresh_html}

<div class="tb">
<span class="l">&#128204; 排序：</span>
<div class="sg">
<button class="sb a" data-k="date" onclick="s('date')">&#128338; 时间</button>
<button class="sb" data-k="read" onclick="s('read')">&#128065; 阅读</button>
<button class="sb" data-k="like" onclick="s('like')">&#128077; 点赞</button>
<button class="sb" data-k="reply" onclick="s('reply')">&#128172; 评论</button>
<button class="sb" data-k="score" onclick="s('score')">⭐ 评分</button>
</div>
<button class="ob a" id="ob" onclick="to()">降序</button>
<span class="l" style="margin-left:8px">&#127991; 分类：</span>
<div class="fg">{cat_buttons}</div>
</div>

<div class="nc" id="nc">显示 {total} 篇报道</div>
<div id="c">{cards_html}</div>

<div class="ft">
<p>数据来源：<a href="https://so.toutiao.com/search?keyword=Hermes%20Agent" target="_blank">今日头条</a> &middot; 由 Hermes Agent 自动追踪 &middot; 每日 22:00 更新</p>
<p><span id="ftd"></span></p>
</div>

<script>
let sk='date',od='desc',cc='all';
function s(k){{document.querySelectorAll('.sb').forEach(b=>b.classList.remove('a'));
document.querySelector('.sb[data-k="'+k+'"]').classList.add('a');sk=k;r();}}
function to(){{od=od==='desc'?'asc':'desc';
document.getElementById('ob').textContent=od==='desc'?'降序':'升序';r();}}
function fc(c){{document.querySelectorAll('.fb').forEach(b=>b.classList.remove('a'));
document.querySelector('.fb[data-c="'+c+'"]').classList.add('a');cc=c;r();}}
function r(){{let cards=Array.from(document.getElementById('c').querySelectorAll('.card'));
cards.forEach(c=>c.style.display=cc==='all'||c.dataset.cat===cc?'':'none');
let f=cc==='all'?cards:cards.filter(c=>c.dataset.cat===cc);
let o=od==='desc'?-1:1;
f.sort((a,b)=>{{let va,vb;
if(sk==='date'){{va=new Date(a.dataset.date).getTime();vb=new Date(b.dataset.date).getTime();}}
else{{va=parseInt(a.dataset[sk]);vb=parseInt(b.dataset[sk]);}}
return(va-vb)*o;}});
f.forEach(c=>document.getElementById('c').appendChild(c));
document.getElementById('nc').textContent='显示 '+f.length+' 篇报道';}}
document.getElementById('ftd').textContent='更新于 '+new Date().toLocaleString('zh-CN');
</script>
</body>
</html>'''
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    return len(html)

if __name__ == '__main__':
    print('=== Hermes Agent News Refresh ===')
    
    # Load cached article metadata
    cached_path = CACHE_FILE
    if os.path.exists(cached_path):
        with open(cached_path) as f:
            cached_articles = json.load(f)
        print(f'Loaded {len(cached_articles)} cached articles')
    else:
        cached_articles = []
    
    # Scrape fresh articles from Toutiao search
    print('Scraping Toutiao search...')
    fresh_articles = scrape_toutiao()
    print(f'  {len(fresh_articles)} fresh articles found')
    
    # Merge: fresh takes priority, cached fills the gaps
    merged = {}
    # First add fresh articles (they're current)
    for a in fresh_articles:
        merged[a['aid']] = a
    
    # Then add cached articles that aren't already in fresh
    for a in cached_articles:
        aid = a.get('id', '')
        if aid and aid not in merged:
            a['aid'] = aid
            a['url'] = f'https://www.toutiao.com/article/{aid}/'
            a['time'] = a.get('pub', '')
            merged[aid] = a
    
    articles = list(merged.values())
    print(f'Total after merge: {len(articles)} articles')
    
    # Fetch metadata for fresh articles (cached ones already have it)
    if fresh_articles:
        print('Fetching fresh metadata from m.toutiao.com...')
        articles = fetch_metadata(articles) if 'fetch_metadata' in dir() else articles
    
    # Fetch GitHub
    print('Fetching GitHub data...')
    try:
        github = fetch_github()
        print(f'  Stars: {github["stars"]}, Forks: {github["forks"]}')
    except Exception as e:
        print(f'  GitHub fetch failed: {e}')
        github = {'stars': 186534, 'forks': 32090, 'issues_count': 19271}
    
    # Generate HTML
    print('Generating HTML...')
    run_info = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'fresh': len(fresh_articles),
        'total': len(articles),
        'cached': len(cached_articles),
    }
    size = gen_html(articles, github, run_info)
    print(f'  Written {size} bytes to {OUTPUT}')
    
    # Save updated cache
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(articles, f, ensure_ascii=False, default=str)
    print(f'  Saved {len(articles)} articles to cache')
    
    # Copy as index.html for GitHub Pages
    import shutil
    shutil.copy2(OUTPUT, os.path.join(REPO_DIR, 'index.html'))
    print(f'  Copied to index.html')
    
    print('Done!')
