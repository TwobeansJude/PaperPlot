AI 智能图表生成
上传 Excel/CSV 数据文件，通过 AI 对话自动生成学术级图表。

功能特点
智能图表生成：AI 根据数据和分析需求自动选择合适的图表类型
对话式交互：像聊天一样描述需求，AI 自动分析数据并生成图表
代码可编辑：生成的 Python 代码可直接在界面上修改并重新生成
学术级美化：图表自动优化配色、字体、排版，符合学术发表标准
多图表支持：支持柱状图、折线图、热力图、桑基图、环形图、箱线图等
快速开始
1. 安装依赖
cd backend
pip install -r requirements.txt
2. 配置 API Key
cp settings.example.json settings.json
# 编辑 settings.json，填入你的 API Key
3. 启动服务
python app.py
打开浏览器访问 http://localhost:5000

项目结构
├── backend/
│   ├── app.py                # Flask 后端服务
│   ├── static/
│   │   ├── index.html        # 前端页面
│   │   ├── app.js            # 前端交互逻辑
│   │   └── style.css         # 样式文件
│   ├── requirements.txt      # Python 依赖
│   ├── settings.example.json # API 配置模板
│   ├── uploads/              # 上传文件（自动创建）
│   └── plots/                # 生成图片（自动创建）
└── .gitignore
技术栈
后端: Python Flask
前端: 原生 HTML + CSS + JavaScript
AI: DeepSeek API
绘图: Matplotlib, Seaborn, Plotly, pycirclize
