import os
import sys
import json
import uuid
import base64
import subprocess
import tempfile
import traceback
import re
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, send_file
import pandas as pd
import numpy as np
import requests

app = Flask(__name__, static_folder='static', static_url_path='')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
PLOTS_DIR = os.path.join(BASE_DIR, 'plots')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
SESSIONS_FILE = os.path.join(BASE_DIR, 'sessions.json')

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PLOTS_DIR, exist_ok=True)


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'api_key': '', 'api_url': 'https://api.deepseek.com', 'model': 'deepseek-chat'}


def save_settings(settings):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def load_sessions():
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_sessions(sessions):
    with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def read_file_data(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
    return df


def get_file_info(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    filename = os.path.basename(file_path)

    if ext == '.csv':
        sheet_names = ['Sheet1']
        all_dfs = {'Sheet1': pd.read_csv(file_path)}
    else:
        xl = pd.ExcelFile(file_path)
        sheet_names = xl.sheet_names
        all_dfs = {s: pd.read_excel(file_path, sheet_name=s) for s in sheet_names}

    sheets = {}
    for sname, df in all_dfs.items():
        headers = df.columns.tolist()
        dtypes = {str(col): str(df[col].dtype) for col in headers}
        shape = [int(df.shape[0]), int(df.shape[1])]

        numeric_cols = []
        categorical_cols = []
        for col in headers:
            if df[col].dtype in ('int64', 'float64', 'int32', 'float32'):
                numeric_cols.append(str(col))
            else:
                categorical_cols.append(str(col))

        preview = df.head(10).fillna('')
        sample = []
        for _, row in preview.iterrows():
            record = {}
            for col in headers:
                val = row[col]
                if pd.isna(val) or val == '':
                    record[str(col)] = ''
                elif isinstance(val, (np.integer,)):
                    record[str(col)] = int(val)
                elif isinstance(val, (np.floating,)):
                    record[str(col)] = float(val)
                elif isinstance(val, np.bool_):
                    record[str(col)] = bool(val)
                else:
                    record[str(col)] = str(val)
            sample.append(record)

        sheets[sname] = {
            'headers': [str(h) for h in headers],
            'dtypes': dtypes,
            'numeric_cols': numeric_cols,
            'categorical_cols': categorical_cols,
            'sample': sample,
            'shape': shape,
        }

    first_sheet = sheets[sheet_names[0]]

    return {
        'filename': filename,
        'file_path': file_path,
        'sheet_names': sheet_names,
        'sheets': sheets,
        'headers': first_sheet['headers'],
        'dtypes': first_sheet['dtypes'],
        'numeric_cols': first_sheet['numeric_cols'],
        'categorical_cols': first_sheet['categorical_cols'],
        'sample': first_sheet['sample'],
        'shape': first_sheet['shape'],
    }


def call_ai_api(prompt, api_key, api_url, model):
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': '你是一个Python数据可视化专家。严格输出JSON格式，不要包含任何思考过程、自言自语、Markdown标记或其他文字。analysis字段只描述图表类型和关键发现，不要写思考过程。'},
            {'role': 'user', 'content': prompt}
        ],
        'temperature': 0.1,
        'max_tokens': 8192,
    }

    resp = requests.post(f'{api_url}/v1/chat/completions', headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data['choices'][0]['message']['content'].strip()


def build_prompt(file_info, requirement, history=None):
    sheet_names = file_info['sheet_names']
    sheets = file_info.get('sheets', {})

    all_sheets_info = []
    for sname in sheet_names:
        sinfo = sheets.get(sname, {})
        sh = sinfo.get('headers', [])
        sd = sinfo.get('dtypes', {})
        sn = sinfo.get('numeric_cols', [])
        sc = sinfo.get('categorical_cols', [])
        ss = sinfo.get('shape', [0, 0])
        sp = sinfo.get('sample', [])

        col_info = [f"    {h} (类型: {sd.get(h, 'unknown')})" for h in sh]
        all_sheets_info.append(f"""  Sheet: {sname}
  形状: {ss[0]}行 × {ss[1]}列
  列:
{chr(10).join(col_info)}
  数值列: {sn}
  分类列: {sc}
  预览:
{json.dumps(sp[:5], ensure_ascii=False, indent=4)}
""")

    history_text = ''
    if history:
        history_text = '\n'.join([
            f"第{i+1}轮: 需求={h['requirement']}, 结果={'成功' if h.get('success') else '失败: '+str(h.get('error',''))}"
            for i, h in enumerate(history)
        ])

    prompt = f"""你是一个Python数据可视化专家。根据用户需求生成Python代码来分析和可视化数据。

【数据信息】
文件名: {file_info['filename']}
共 {len(sheet_names)} 个Sheet: {sheet_names}

{chr(10).join(all_sheets_info)}

【用户需求】
{requirement}

【历史对话】
{history_text if history_text else '无'}

【运行环境版本】
pandas 2.3.3 | matplotlib 3.10.6 | numpy 2.3.5 | plotly 6.3.0 | seaborn 0.13.2 | scipy 1.16.3
注意: pandas 2.x 禁止 Timestamp + int，必须用 Timestamp + pd.Timedelta(n, unit='D')

【代码要求】
1. 可用库: pandas, numpy, matplotlib, seaborn, scipy, plotly, pycirclize
   - 强制规则: 有现成专用函数就**必须**用它，禁止用低级API手搓
   - 每个库的详细 API 签名如下，直接对照写代码:

   === pandas ===
     pd.read_excel(io, sheet_name=0, header=0, ...)  # io=文件路径, sheet_name=None读取所有Sheet
     pd.pivot_table(data, values, index, columns, aggfunc='mean', fill_value=0)
     pd.to_datetime(arg, errors='raise', format=None)
     pd.Timedelta(value, unit=None)  # e.g. pd.Timedelta(7, unit='D')
     df.melt(id_vars, value_vars, var_name, value_name='value')  # 宽表转长表
     df.groupby(by=None, as_index=True)  # 分组聚合
     df.set_index(keys)  # 设置索引列
     df.reset_index(drop=False)  # 重置索引
     df.sort_values(by, ascending=True)  # 排序

   === plotly.express ===
     px.timeline(data_frame, x_start, x_end, y, color, ...)
     px.bar(data_frame, x, y, color, ...)
     px.line(data_frame, x, y, color, ...)
     px.scatter(data_frame, x, y, color, ...)
     px.pie(data_frame, names, values, ...)
     保存: fig.write_image(output_path, format='png', width=1200, height=600)

   === plotly.graph_objects ===
     fig = go.Figure(data=[go.Sankey(node=dict(label=[...]), link=dict(source=[...], target=[...], value=[...]))])
     fig.write_image(output_path, format='png')

   === seaborn (sns) ===
     sns.heatmap(data, vmin=None, vmax=None, cmap=None, annot=None, fmt='.2g', linewidths=0, ...)
     sns.clustermap(data, figsize=(10,10), cmap=None, ...)
     sns.pairplot(data, hue=None, kind='scatter', ...)
     sns.boxplot(data=None, x=None, y=None, hue=None, ...)
     sns.violinplot(data=None, x=None, y=None, hue=None, ...)
     sns.barplot(data=None, x=None, y=None, hue=None, estimator='mean', ...)
     sns.lineplot(data=None, x=None, y=None, hue=None, ...)
     sns.scatterplot(data=None, x=None, y=None, hue=None, ...)
     sns.histplot(data=None, x=None, y=None, bins='auto', ...)
     sns.kdeplot(data=None, x=None, y=None, fill=None, ...)
     sns.set_style(style=None, rc=None)
     sns.set_context(context=None, font_scale=1, rc=None)

   === matplotlib ===
     fig, ax = plt.subplots(figsize=(10,6))
     ax.set_title(label, fontsize=16, fontweight='bold', pad=15)
     ax.set_xlabel(xlabel, fontsize=13, fontweight='medium')
     ax.set_ylabel(ylabel, fontsize=13, fontweight='medium')
     ax.set_xlim(left, right) / ax.set_ylim(bottom, top)
     ax.set_xticks(ticks) / ax.set_xticklabels(labels, rotation=30, ha='right')
     ax.tick_params(axis='both', labelsize=11)  # 注意: ha参数不能在这里！
     ax.legend(loc='best', fontsize=11, frameon=True, fancybox=True, framealpha=0.9, edgecolor='#ddd')
     ax.bar(x, height, width=0.6, edgecolor='white', linewidth=0.5, alpha=0.85)
     ax.plot(x, y, linewidth=2.5, marker='o', markersize=7, markeredgecolor='white', markeredgewidth=0.5)
     ax.scatter(x, y, s=60, alpha=0.75, edgecolors='white', linewidth=0.5)
     ax.pie(x, labels, autopct='%1.1f%%', pctdistance=0.75, startangle=90)
     ax.grid(alpha=0.3, linestyle='--', linewidth=0.5)
     fig.savefig(output_path, dpi=200, bbox_inches='tight')
     plt.close(fig)

   === scipy.interpolate ===
     make_interp_spline(x, y, k=3, bc_type=None)  # 平滑曲线，禁止用 matplotlib.bezier

   === matplotlib.dates ===
     mdates.date2num(d)  # Timestamp → 数值
     mdates.num2date(x)  # 数值 → Timestamp

   === pycirclize ===
     Circos(sectors: dict[str, float|tuple], start=0, end=360, space=0)
     Circos.chord_diagram(matrix: pd.DataFrame, cmap='viridis', ticks_interval=None)
     Circos.initialize_from_matrix(matrix, start=-270, end=-90)
     circos.savefig(savefile, dpi=100, figsize=(8,8))
     sector.add_track(r_lim: tuple[float,float]) → Track
   Track 方法（x/y/height/color 均可传数组，类似 matplotlib，无需循环！）:
     bar(x, height, width=0.8, color=None, edgecolor='white', linewidth=0.5, **kwargs)
     line(x, y, **kwargs)
     scatter(x, y, s=None, c=None, **kwargs)
     heatmap(data, **kwargs)
     fill_between(x, y1, y2=0)
     text(text, x, r, ...)
     axis(**kwargs)
     xticks(x, labels: list[str]|None)  # 不支持 size/labelsize/fontsize！
     yticks(y, labels: list[str]|None)  # 不支持 size/labelsize/fontsize！
   【Track 方法关键规则】
     - x 和 height/y 必须是数组/列表，且长度相同
     - color 可以是单个颜色字符串，也可以是颜色列表（长度与数据一致）
     - 错误: for j in range(N): track.bar(x[j], val, color=c[j]) → x[j] 是标量，bar() 要求数组！
     - 正确: track.bar(x, values, color=colors)  → 一次传入整个数组，color 也是数组
     - 完整示例（5个条件在1个扇区）:
       sector_size = 10
       x = np.linspace(0.5, sector_size - 0.5, 5)  # 5个均匀位置
       values = [row['E1'], row['E2'], row['E3'], row['E4'], row['E5']]
       colors = sns.color_palette("Set2", n_colors=5)
       track.bar(x, values, width=0.8, color=colors, edgecolor='white', linewidth=0.5)
     【重要】x 值必须在 [0, sector_size] 范围内！
       - sector_size 是 Circos(sectors={{'A': 10}}) 中定义的值，Track 的 x 轴范围就是 [0, sector_size]
       - 正确: sector_size = 10; x = np.linspace(0.5, sector_size - 0.5, len(values))
       - 正确: sector_size = 10; x = np.arange(1, len(values)+1)  # 前提: len(values) <= sector_size
       - 正确: x = np.arange(len(values)) + 0.5  # 前提: sector_size >= len(values)
       - 错误: x = np.arange(len(values))  # 当 sector_size=1 时，x=[0,1,2,...] 越界！
       - 错误: track.bar(values, color='red')  # 缺 height 参数
       - 错误: track.bar(['A','B'], values)     # x 不能是字符串
     【Circos 轨道布局 - 关键！决定图的外观】
       - r_lim=(r_start, r_end) 定义轨道在半径上的位置，范围 0~100（0=圆心，100=最外圈）
       - 必须把轨道均匀分布在整个半径空间，不要把数据挤在最外层！
       - 错误: sector.add_track((95, 100)) → 轨道只在最外层5%，内部95%全是空白！
       - 单轨道: sector.add_track((30, 90))  → 占半径 30~90，留中间放标签
       - 双轨道: track1=(30, 65), track2=(70, 95) → 两个轨道均匀分布
       - 三轨道: track1=(30, 52), track2=(55, 77), track3=(80, 95)
       - 轨道之间留 3~5 的间距，最内层轨道从 25~35 开始（留中心空间）
       - sector.text(text, r=半径, ...) → r 必须在 0~100 范围内，通常放在轨道外侧 (r=98)
       - track.axis()  → 为轨道添加 x 轴刻度线（建议每个轨道都调用）
       - track.xticks(x, labels) / track.yticks(y, labels) → 设置刻度标签
     【Circos 遍历扇区 - 唯一正确方式】
       - Circos 对象不支持下标访问！circos[name] 会崩溃！
       - 错误: sector = circos[sample]  → TypeError: 'Circos' object is not subscriptable
       - 正确: for sector in circos.sectors: 然后用 sector.name 判断
       - 正确: for i, sector in enumerate(circos.sectors): row = df.iloc[i]
   【Circos 图最佳实践】
       - 染色体/基因组数据: sectors 用实际长度 (如 {{'chr1': 248956422}})，保持生物学比例
       - 普通分类数据: sectors 用统一值 (如每个 10)，确保 x 值不越界
       - 热力图/矩阵数据: 优先使用 Circos.initialize_from_matrix(df) 自动处理
       - 样本标签: sector.text(sector.name, x=sector_size/2, r=98, size=10, ha='center', va='center')
       - 图表外侧标题: 用 circos.savefig() + matplotlib 的 fig.suptitle()，不要写在 pycirclize 内

2. 数据文件路径是: {file_info['file_path']}
3. 读取所有Sheet: dfs = pd.read_excel(r'{file_info['file_path']}', sheet_name=None)
   - dfs 是一个字典，key是Sheet名称，value是对应的DataFrame
   - 例如: dfs['chr_ideogram'], dfs['track_bar'], dfs['link']
4. 图表保存到: output_path (变量已预定义)
5. matplotlib 已设为 Agg 后端: matplotlib.use('Agg')（无需调用 plt.show()）
6. 字体配置（学术规范：中文宋体、英文Times New Roman）：
   plt.rcParams['font.sans-serif'] = ['SimSun', 'DejaVu Sans']
   plt.rcParams['font.family'] = 'sans-serif'
   plt.rcParams['axes.unicode_minus'] = False
   SimSun内嵌的拉丁字符即为Times New Roman风格，满足中英文混排学术要求
7. 注意: sns.set_style/sns.set_context 会重置字体，务必在 seaborn 设置之后重新执行上述字体配置
8. 代码放在 try-except 块中，出错时打印错误信息

【图表美化 - 学术级视觉规范】
**全局样式（必须设置）：**
- 使用 sns.set_style("whitegrid") 开启干净网格背景
- 使用 sns.set_context("notebook", font_scale=1.2) 设置合适的字体比例
- 图形尺寸: figsize=(10, 6) 或根据数据量调整，宽高比约 1.6:1
- 分辨率: dpi=200，确保高清输出
- 背景色: figure.patch 设为白色，axes.facecolor 设为 '#f8f9fa'（浅灰）

**配色方案（按优先级选择）：**
- 首选: sns.color_palette("Set2") — 柔和学术配色
- 备选: sns.color_palette("husl", n_colors=N) — 均匀分布的高饱和度色
- 单色渐变: sns.light_palette("#4f46e5", n_colors=N) 或 sns.cubehelix_palette(N)
- 对比色: ['#4f46e5', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4']
- 禁止使用 matplotlib 默认的 tab10/tab20 配色

**字体与排版：**
- 标题: fontsize=16, fontweight='bold', pad=15
- 轴标签: fontsize=13, fontweight='medium'
- 刻度标签: fontsize=11
- 图例: fontsize=11, frameon=True, fancybox=True, framealpha=0.9, edgecolor='#ddd'
- 数值标签(如有): fontsize=10, fontweight='bold', color='#333'

**图表元素美化：**
- 网格线: alpha=0.3, linestyle='--', linewidth=0.5
- 坐标轴边框(spines): 颜色设为 '#cccccc', linewidth=0.8
- 顶部和右侧spines设为不可见: ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
- 柱状图: 柱子宽度0.6-0.7, edgecolor='white', linewidth=0.5, alpha=0.85
- 折线图: linewidth=2.5, marker='o', markersize=7, markeredgecolor='white', markeredgewidth=0.5
- 散点图: s=60-100, alpha=0.75, edgecolors='white', linewidth=0.5
- 饼图: autopct='%1.1f%%', pctdistance=0.75, explode适当突出, shadow=False, startangle=90
- 热力图: cmap='RdYlBu_r' 或 'coolwarm', annot=True, fmt='.1f', linewidths=0.5, linecolor='white'

**布局优化：**
- 使用 plt.tight_layout(pad=2.0) 避免元素重叠
- 多子图使用 plt.subplots_adjust(hspace=0.3, wspace=0.3)
- x轴标签过长时旋转30-45度: ax.tick_params(axis='x', rotation=30, labelsize=11); plt.setp(ax.get_xticklabels(), ha='right')
- 数值轴添加千分位逗号: from matplotlib.ticker import FuncFormatter

**学术规范：**
- 图表必须包含清晰的标题和轴标签（含单位如有）
- 图例位置自动选择最佳位置: loc='best'
- 数据排序合理（降序排列使图表更易读）
- 避免3D效果、过度装饰、emoji

【数据类型安全 - 最高优先级】
- 绝对不要对分类列进行 float() 或 astype(float) 转换
- 分类列只能用作标签、分组键、x轴刻度
- 数值列才能用于数学运算
- 各Sheet的分类列请参考上方数据信息中的"分类列"字段

【注意事项】
- 禁止对字符串列进行数值转换

【桑基图使用说明】
桑基图推荐使用 plotly（kaleido 已安装，可直接导出PNG）：
  import plotly.graph_objects as go
  fig = go.Figure(data=[go.Sankey(
      node=dict(label=[...]),
      link=dict(source=[索引列表], target=[索引列表], value=[数值列表])
  )])
  fig.write_image(output_path, format='png')
也可用 matplotlib.sankey（flows 必须是一维数组）：
  Sankey().add(flows=[流入值1, 流入值2, ..., -总流出], orientations=[0]*N, labels=[...])
多来源×多目标时用 pd.pivot_table() 构建交叉表后改用热力图或柱状图。

【matplotlib.bezier 说明】
- matplotlib.bezier 模块中的 make_interp_spline 已在 matplotlib 3.10+ 中移除
- 如需平滑曲线/样条插值，请使用 scipy.interpolate.make_interp_spline

【高频 API 错误 - 绝对禁止以下写法（已验证会崩溃）】
1. ax.tick_params(axis='x', ha='right')  → ValueError！tick_params 没有 ha 参数
   正确: ax.tick_params(axis='x', rotation=30, labelsize=11); plt.setp(ax.get_xticklabels(), ha='right')
2. pycirclize track 中 x 值超出 [0, sector_size] 范围 → 运行时崩溃
   错误: sectors={{'A':1}}; x=np.arange(5); track.bar(x, values)  # x=[0,1,2,3,4] 越界！
   正确: sectors={{'A':10}}; x=np.arange(1,6); track.bar(x, values)  # 保证 x <= sector_size
   或: x=np.linspace(0.5, sector_size-0.5, len(values))  # 均匀分布在范围内
3. circos[sample] → TypeError！Circos 对象不支持下标访问，必须用 for sector in circos.sectors 遍历
4. pd.Timestamp + int → pandas 2.x 禁止！用 pd.Timestamp + pd.Timedelta(n, unit='D')
5. scipy.interpolate.make_interp_spline → 已从 matplotlib.bezier 移除，必须从 scipy 导入

【输出格式】
只输出一个JSON对象:
{{"analysis": "数据分析思路简述", "code": "Python代码"}}
"""

    return prompt


def sanitize_code(code):
    '''修复 AI 生成的常见 API 错误（确定性修复，不依赖 AI）'''
    import re

    # --- 修复1: tick_params 中的 ha= 参数（ha 不是 tick_params 的有效参数，会抛 ValueError）---
    def fix_tick_params(m):
        prefix = m.group(1)
        args = m.group(2)
        args = re.sub(r'\bha\s*=\s*[\'"]\w+[\'"]', '', args)
        args = re.sub(r',\s*,', ',', args)
        args = args.strip().strip(',').strip()
        return f'{prefix}.tick_params({args})'

    code = re.sub(r'(\w+)\.tick_params\(([^)]+)\)', fix_tick_params, code)

    # --- 修复2: matplotlib.bezier.make_interp_spline → scipy（matplotlib 3.10+ 已移除）---
    code = re.sub(
        r'from\s+matplotlib\.bezier\s+import\s+make_interp_spline',
        'from scipy.interpolate import make_interp_spline',
        code
    )
    code = re.sub(
        r'matplotlib\.bezier\.make_interp_spline',
        'make_interp_spline',
        code
    )

    # --- 修复3: ax.set_title 中 fontweight → fontweight（两者都可用，统一为 matplotlib 规范拼写）---
    code = re.sub(r'(\.set_title\([^)]*)\bfontweight\b', r'\1fontweight', code)
    code = re.sub(r'(\.set_xlabel\([^)]*)\bfontweight\b', r'\1fontweight', code)
    code = re.sub(r'(\.set_ylabel\([^)]*)\bfontweight\b', r'\1fontweight', code)

    # --- 修复4: circos[name] → 遍历查找（Circos 不支持下标访问）---
    code = re.sub(
        r'(\w+)\s*=\s*circos\[(\w+)\]',
        r"\1 = next((s for s in circos.sectors if s.name == \2), None)",
        code
    )

    # --- 修复5: track.xticks/yticks 中 size= → 移除（pycirclize 不支持 size 参数）---
    def strip_size_from_ticks(m):
        call = m.group(1)       # e.g. "track.xticks" or "track.yticks"
        args = m.group(2)       # everything inside (...)
        # Step 1: Remove "size=N" (just the key=value, not surrounding commas)
        args = re.sub(r'\bsize\s*=\s*\d+', '', args)
        # Step 2: Clean up double commas ", ," => ","
        args = re.sub(r',\s*,', ',', args)
        # Step 3: Clean up leading/trailing commas
        args = args.strip().strip(',').strip()
        return f'{call}({args})'

    code = re.sub(
        r'(\w+\.(?:xticks|yticks))\(([^)]+)\)',
        strip_size_from_ticks,
        code
    )

    return code


def execute_code(code, file_path):
    code = sanitize_code(code)
    output_path = os.path.join(PLOTS_DIR, f'output_{uuid.uuid4().hex[:8]}.png')

    if os.path.exists(output_path):
        os.remove(output_path)

    indented = '\n'.join('    ' + line for line in code.split('\n'))

    wrapper = f'''import sys, os, traceback, base64, warnings
warnings.filterwarnings('ignore')

# Fix Windows GBK encoding issue - force UTF-8 for stdout/stderr
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

user_pkg = os.path.join(os.path.expanduser('~'), '.local', 'lib', 'site-packages')
if os.path.exists(user_pkg) and user_pkg not in sys.path:
    sys.path.insert(0, user_pkg)

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
import pycirclize

plt.rcParams['font.sans-serif'] = ['SimSun', 'DejaVu Sans']
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False

# NOTE: sns.set_style/sns.set_context will reset fonts.
# After calling any seaborn style function, re-run the font lines above.

output_path = {repr(output_path)}

try:
{indented}
except Exception as e:
    print("ERROR:" + str(e), file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

if os.path.exists(output_path):
    with open(output_path, 'rb') as f:
        img = base64.b64encode(f.read()).decode()
    print("IMG:" + img[:50] + "...")
else:
    print("NO_FILE", file=sys.stderr)
    sys.exit(1)
'''

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(wrapper)
        tmp = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp],
            capture_output=True, text=True, timeout=120,
            cwd=BASE_DIR
        )
        if result.returncode == 0 and os.path.exists(output_path):
            with open(output_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode()
            return {'success': True, 'image': img_b64, 'path': output_path}
        else:
            err = (result.stderr or '').strip() or (result.stdout or '').strip()
            return {'success': False, 'error': err[:600]}
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': '代码执行超时(120秒)'}
    finally:
        try:
            os.unlink(tmp)
        except:
            pass


@app.route('/')
def index():
    return send_file(os.path.join(BASE_DIR, 'static', 'index.html'))


@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'GET':
        s = load_settings()
        s['api_key'] = s.get('api_key', '')[:8] + '****' if len(s.get('api_key', '')) > 8 else ''
        return jsonify({'success': True, 'settings': s})
    else:
        data = request.get_json()
        s = {
            'api_key': data.get('api_key', ''),
            'api_url': data.get('api_url', 'https://api.deepseek.com'),
            'model': data.get('model', 'deepseek-chat'),
        }
        save_settings(s)
        return jsonify({'success': True})


@app.route('/api/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '请选择文件'})

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': '文件名为空'})

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls', '.csv'):
        return jsonify({'success': False, 'error': '仅支持 .xlsx .xls .csv 文件'})

    safe_name = f"{uuid.uuid4().hex[:12]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    file.save(file_path)

    try:
        info = get_file_info(file_path)
        session_id = uuid.uuid4().hex[:16]
        sessions = load_sessions()
        sessions[session_id] = {
            'file_info': info,
            'history': [],
            'created_at': datetime.now().isoformat(),
        }
        save_sessions(sessions)

        return jsonify({
            'success': True,
            'session_id': session_id,
            'file_info': info,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'文件解析失败: {str(e)}'})


@app.route('/api/generate', methods=['POST'])
def generate():
    data = request.get_json()
    session_id = data.get('session_id', '')
    requirement = data.get('requirement', '').strip()

    if not requirement:
        return jsonify({'success': False, 'error': '请输入需求描述'})

    settings = load_settings()
    api_key = data.get('api_key') or settings.get('api_key', '')
    api_url = data.get('api_url') or settings.get('api_url', 'https://api.deepseek.com')
    model = data.get('model') or settings.get('model', 'deepseek-chat')

    if not api_key:
        return jsonify({'success': False, 'error': '请先设置API Key'})

    sessions = load_sessions()
    session = sessions.get(session_id, {})
    file_info = session.get('file_info', {})

    if not file_info:
        return jsonify({'success': False, 'error': '会话已过期，请重新上传文件'})

    history = session.get('history', [])

    try:
        prompt = build_prompt(file_info, requirement, history)
        response = call_ai_api(prompt, api_key, api_url, model)

        result = None
        try:
            result = json.loads(response)
        except:
            m = re.search(r'```json\s*(.*?)```', response, re.DOTALL)
            if m:
                result = json.loads(m.group(1))
            else:
                m = re.search(r'```python\s*(.*?)```', response, re.DOTALL)
                if m:
                    result = {'analysis': 'AI生成', 'code': m.group(1).strip()}
                else:
                    m = re.search(r'```\s*(.*?)```', response, re.DOTALL)
                    if m:
                        result = {'analysis': 'AI生成', 'code': m.group(1).strip()}

        if not result or 'code' not in result:
            return jsonify({'success': False, 'error': f'AI响应解析失败: {response[:300]}'})

        code = result.get('code', '')
        analysis = result.get('analysis', '')

        exec_result = execute_code(code, file_info['file_path'])

        history.append({
            'requirement': requirement,
            'analysis': analysis,
            'success': exec_result.get('success', False),
            'error': exec_result.get('error', '') if not exec_result.get('success') else '',
            'timestamp': datetime.now().isoformat(),
        })
        session['history'] = history
        sessions[session_id] = session
        save_sessions(sessions)

        if exec_result.get('success'):
            return jsonify({
                'success': True,
                'image': exec_result['image'],
                'analysis': analysis,
                'code': code,
                'history': history,
            })
        else:
            return jsonify({
                'success': False,
                'error': exec_result.get('error', '代码执行失败'),
                'analysis': analysis,
                'code': code,
                'history': history,
            })

    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': f'API调用失败: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'系统错误: {str(e)}\n{traceback.format_exc()[:300]}'})


@app.route('/api/download/<filename>')
def download(filename):
    return send_from_directory(PLOTS_DIR, filename, as_attachment=True)


@app.route('/api/execute_code', methods=['POST'])
def api_execute_code():
    try:
        data = request.get_json()
        if not data or 'code' not in data:
            return jsonify({'success': False, 'error': '缺少 code 参数'})

        code = data['code']
        if not code or not isinstance(code, str):
            return jsonify({'success': False, 'error': 'code 参数无效'})

        result = execute_user_code(code)
        if result.get('success'):
            return jsonify({
                'success': True,
                'image': result['image'],
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', '执行失败')
            })
    except Exception as e:
        return jsonify({'success': False, 'error': f'系统错误: {str(e)}'})


def execute_user_code(code):
    output_path = os.path.join(PLOTS_DIR, f'user_{uuid.uuid4().hex[:8]}.png')

    if os.path.exists(output_path):
        os.remove(output_path)

    indented = '\n'.join('    ' + line for line in code.split('\n'))

    wrapper = f'''import sys, os, traceback, base64, warnings
warnings.filterwarnings('ignore')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

user_pkg = os.path.join(os.path.expanduser('~'), '.local', 'lib', 'site-packages')
if os.path.exists(user_pkg) and user_pkg not in sys.path:
    sys.path.insert(0, user_pkg)

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
import pycirclize

plt.rcParams['font.sans-serif'] = ['SimSun', 'DejaVu Sans']
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False

output_path = {repr(output_path)}

try:
{indented}
except Exception as e:
    print("ERROR:" + str(e), file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

if os.path.exists(output_path):
    with open(output_path, 'rb') as f:
        img = base64.b64encode(f.read()).decode()
    print("IMG:" + img[:50] + "...")
else:
    print("NO_FILE", file=sys.stderr)
    sys.exit(1)
'''

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(wrapper)
        tmp = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp],
            capture_output=True, text=True, timeout=120,
            cwd=BASE_DIR
        )
        if result.returncode == 0 and os.path.exists(output_path):
            with open(output_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode()
            return {'success': True, 'image': img_b64, 'path': output_path}
        else:
            err = (result.stderr or '').strip() or (result.stdout or '').strip()
            return {'success': False, 'error': err[:600]}
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': '代码执行超时(120秒)'}
    finally:
        try:
            os.unlink(tmp)
        except:
            pass


if __name__ == '__main__':
    print(f'Starting AI Visualization Server on port 5000...')
    print(f'API: http://localhost:5000')
    app.run(host='0.0.0.0', port=5000, debug=False)