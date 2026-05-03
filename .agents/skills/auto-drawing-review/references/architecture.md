# 技术架构详解

## 一、前端层

### React
现代JavaScript SPA框架。在本系统中负责：图纸上传界面（拖拽+进度条）、PDF预览与坐标交互（点击图纸元素定位到审图问题）、审图结果可视化（红/黄标注）。选型理由：生态丰富，PDF.js、Canvas库成熟。

### PDF.js（Mozilla）
浏览器端PDF解析与渲染引擎。负责：前端直接预览PDF（无需后端转图）、获取用户点击的页面坐标`(x,y)`传给后端关联问题、提取可选文字层做前端高亮。Chrome/Firefox内置PDF查看器均基于此。

### Axios
HTTP客户端。负责调用后端API、监控大文件上传进度、统一处理认证Token和错误提示。

---

## 二、后端/API层

### FastAPI
高性能Python异步Web框架。负责：RESTful API接口、自动Swagger文档、Pydantic数据校验、async/await高并发。相比Flask/Django，FastAPI的异步原生支持对并发调用大模型API至关重要。

### Pydantic
数据校验与序列化库。负责：定义图纸JSON模型（Room/Window等）、自动校验大模型返回的JSON字段完整性、类型转换（字符串"12.5"→浮点数）。FastAPI底层依赖它。

### Uvicorn
ASGI服务器。负责：运行FastAPI，用异步IO同时处理数百连接，避免传统WSGI的一个请求一个线程模式。

### Celery + Redis
分布式任务队列。负责：审图流水线异步执行（PDF转图→大模型解析→规则校验→生成报告）、任务状态追踪、失败重试、定时清理。审图耗时30秒~5分钟，必须用任务队列避免HTTP超时。

---

## 三、PDF解析层

### PyMuPDF（fitz）
底层基于C语言MuPDF的PDF处理库。负责：**PDF转高清图片**（300 DPI喂给大模型）、提取文字块及精确坐标`[x0,y0,x1,y1]`、提取矢量线条信息。比PyPDF2/pdfplumber快10倍，处理复杂CAD导出PDF极稳定。

### PaddleOCR（百度开源）
面向中文优化的深度学习OCR。负责：识别扫描件/图片型PDF中的中文标注（如"卫生间"、"C1521"）、识别图纸表格（门窗表、材料表）、印章与手写批注。中文工程符号（如"3Φ20"）识别率业界顶尖，可私有化部署保护图纸隐私。

### EasyOCR
备用OCR。负责：多语言图纸（外资项目英文标注）识别，与PaddleOCR互补。

### pdf2image
PDF转图封装库（底层调用poppler）。负责：快速批量转图，配置简单但控制力弱于PyMuPDF，适合原型开发。

### ezdxf
纯Python DXF读写库。负责：**未来扩展**：直接读取AutoCAD原生DXF的几何数据（线条、圆弧、图层、块引用）、生成带批注的DXF修改建议图。当前MVP以PDF为主，此库为二期预留。

---

## 四、AI/大模型层

### GPT-4V / GPT-4o（OpenAI）
多模态大模型。负责：将PDF图纸图片解析为结构化JSON（房间、门窗、尺寸）、根据规则引擎输出生成自然语言修改意见（专业工程语境）。MVP阶段首选，按Token计费。

### Claude 3（Anthropic）
多模态大模型。负责：与GPT-4V形成双模型投票（交叉验证降低幻觉）、利用200K超长上下文一次性分析多页图纸+规范文本。

### Qwen-VL（阿里通义千问）
开源多模态大模型。负责：**私有化部署**（内网离线审图，满足设计院保密要求）、中文工程图纸专门优化、成本远低于OpenAI API。

### Transformers（HuggingFace）
ML模型统一接口库。负责：本地加载Qwen-VL/LLaVA等开源模型、领域微调（Fine-tuning）训练专用图纸理解模型、`pipeline("image-to-text")`快速搭建流程。

### PyTorch
深度学习框架。负责：作为PaddleOCR、Transformers、YOLO的底层引擎；训练自定义模型（门窗识别、尺寸检测）；GPU加速（CUDA）。

### YOLOv8（Ultralytics）
实时目标检测算法。负责：在图纸图片上快速框出建筑元素（门、窗、楼梯、电梯、尺寸线、轴号），单页<100ms。输出边界框坐标，辅助大模型聚焦重点区域。

### DETR（Facebook）
基于Transformer的检测模型。负责：图纸版面分析（区分标题栏/平面图主体/文字说明/图例区）、检测元素间指向关系（尺寸线→被标注墙体）。

---

## 五、数据存储层

### PostgreSQL
关系型数据库。负责：存项目、图纸元数据、审图结果、规范条文。核心优势：JSONB字段可直接存储图纸JSON并支持内部查询（如"找出所有卧室面积<9㎡的项目"）。PostGIS扩展支持未来空间分析（如防火门间距）。

### MongoDB
文档型数据库。负责：存储大模型原始输出（Schema-free适应结构变化）、图纸版本历史、审图完整调用日志。不作为主业务库，与PG配合使用。

### SQLite
嵌入式数据库。负责：开发环境快速调试、桌面离线版审图工具缓存、规则库本地缓存。

### Redis
内存数据结构存储。负责：Celery任务队列、API限流（防疯狂调大模型）、用户会话缓存、热点规范条文缓存（<1ms响应）、WebSocket进度推送（Pub/Sub）。

---

## 六、规则引擎与报告层

### JSON Schema
声明式JSON结构描述语言。负责：约束大模型输出格式、前后端接口契约、自动生成表单。

### Jinja2
Python模板引擎。负责：生成HTML审图报告（带图纸缩略图、问题列表、规范引用）、生成Markdown/Word中间格式。

### ReportLab / FPDF2
PDF生成库。负责：合成正式PDF审图报告（带公司LOGO、页眉页脚）、在图纸PDF上盖"审核通过/问题清单"印章。ReportLab功能极强但学习曲线陡；FPDF2轻量适合表格化报告。

### WeasyPrint / pdfkit
HTML+CSS→PDF转换工具。负责：先用Jinja2生成精美HTML报告，再转PDF。比直接用ReportLab手写代码排版效率高10倍，且样式与前端网页一致。

---

## 七、基础设施层

### Nginx
反向代理与静态文件服务器。负责：SSL证书管理、将请求转发给Uvicorn、直接提供前端静态文件/上传的PDF/生成的报告（比Python快100倍）、负载均衡、调整`client_max_body_size`允许100MB+图纸包上传。

### Docker + Docker Compose
容器化编排。负责：确保开发/测试/生产环境完全一致（Python版本、系统库、字体）、一键启动多服务（前端/后端/Worker/Redis/Postgres/Nginx）、大模型服务封装（带CUDA驱动、PyTorch、显存分配）。

### Git + Git LFS
版本控制。负责：代码版本管理、图纸PDF大文件版本追踪（Git LFS）、CI/CD自动测试与部署。
