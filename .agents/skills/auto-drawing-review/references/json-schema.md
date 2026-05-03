# 图纸JSON数据结构（DrawingJSON Schema）

本Schema是大模型解析图纸后的**唯一标准输出格式**，也是规则引擎的输入格式。任何修改需同步更新本文件与后端Pydantic模型。

## 根对象：DrawingJSON

```json
{
  "version": "1.0.0",
  "drawing_info": {
    "project_name": "示例小区1#住宅",
    "drawing_no": "建施-05",
    "drawing_name": "一层平面图",
    "scale": "1:100",
    "design_unit": "XX设计院",
    "date": "2024-03-15"
  },
  "building_info": {
    "building_type": "住宅",
    "building_height": 78.5,
    "floors_above_ground": 26,
    "floors_under_ground": 1,
    "fire_resistance_rating": "一级",
    "structure_type": "剪力墙"
  },
  "spaces": [],
  "walls": [],
  "doors": [],
  "windows": [],
  "stairs": [],
  "elevators": [],
  "annotations": [],
  "dimensions": [],
  "coordinate_system": {
    "unit": "mm",
    "origin": {"x": 0, "y": 0},
    "page_width": 42000,
    "page_height": 29700
  }
}
```

## 核心子结构

### Space（空间/房间）

```json
{
  "id": "R01",
  "name": "主卧",
  "name_alt": "卧室",
  "category": "卧室",
  "floor": 1,
  "area": 15.2,
  "perimeter": 16.8,
  "bbox": {"x1": 1200, "y1": 2400, "x2": 5100, "y2": 5400},
  "vertices": [[1200,2400], [5100,2400], [5100,5400], [1200,5400]],
  "windows": ["W01", "W02"],
  "doors": ["D01"],
  "height": 2800,
  "floor_elevation": 0.000,
  "is_enclosed": true,
  "natural_lighting": true,
  "ventilation": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | ✅ | 全局唯一，如"R01" |
| name | string | ✅ | 图纸上标注的名称 |
| category | enum | ✅ | 枚举：卧室/客厅/厨房/卫生间/楼梯间/电梯间/走廊/阳台/储藏/设备间/其他 |
| area | float | ✅ | 使用面积，单位㎡ |
| bbox | object | ✅ | 边界框，单位与coordinate_system一致（默认mm） |
| vertices | array | | 多边形顶点坐标，用于精确几何计算 |
| windows/doors | array | | 关联的门窗ID列表 |
| height | float | | 层高，单位mm |
| is_enclosed | bool | | 是否围合空间 |
| natural_lighting | bool | | 是否有自然采光（大模型判断） |
| ventilation | bool | | 是否有自然通风（大模型判断） |

### Window（外窗）

```json
{
  "id": "W01",
  "room_id": "R01",
  "width": 1500,
  "height": 1500,
  "sill_height": 900,
  "head_height": 2400,
  "type": "平开窗",
  "fire_resistance_hours": 0,
  "glass_spec": "6+12A+6",
  "opening_direction": "外平开",
  "bbox": {"x1": 3000, "y1": 2400, "x2": 4500, "y2": 3900},
  "orientation": "南"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | ✅ | 全局唯一 |
| room_id | string | ✅ | 所属房间ID |
| width/height | float | ✅ | 洞口宽/高，mm |
| sill_height | float | ✅ | 窗台高度，mm |
| fire_resistance_hours | float | | 耐火完整性小时数，0表示无要求 |
| orientation | enum | | 朝向：东/南/西/北/东南/东北/西南/西北 |

### Door（门）

```json
{
  "id": "D01",
  "room_id_from": "R01",
  "room_id_to": "走廊",
  "width": 900,
  "height": 2100,
  "type": "平开门",
  "fire_rating": "乙级",
  "fire_resistance_hours": 1.0,
  "opening_direction": "向内",
  "bbox": {"x1": 5100, "y1": 3600, "x2": 6000, "y2": 5700},
  "is_exit": false,
  "is_elevator_lobby_door": false
}
```

### Wall（墙体）

```json
{
  "id": "WL01",
  "start": [1200, 2400],
  "end": [5100, 2400],
  "thickness": 200,
  "type": "剪力墙",
  "is_load_bearing": true,
  "is_fire_wall": false,
  "fire_resistance_hours": 3.0,
  "bbox": {"x1": 1200, "y1": 2300, "x2": 5100, "y2": 2500}
}
```

### Stair（楼梯）

```json
{
  "id": "S01",
  "stair_type": "防烟楼梯间",
  "width": 1500,
  "tread_width": 280,
  "riser_height": 175,
  "landing_width": 1500,
  "direction": "双跑",
  "is_enclosed": true,
  "has_natural_vent": true,
  "has_fire_door": true,
  "fire_door_rating": "乙级",
  "bbox": {...}
}
```

### Dimension（尺寸标注）

```json
{
  "id": "Dim01",
  "value": 3600,
  "unit": "mm",
  "start_point": [1200, 1800],
  "end_point": [4800, 1800],
  "text_position": [3000, 1700],
  "associated_elements": ["WL01", "WL02"],
  "is_verified": false
}
```

### Annotation（文字标注）

```json
{
  "id": "Ann01",
  "text": "卫生间",
  "position": [3200, 4000],
  "font_height": 350,
  "rotation": 0,
  "bbox": {"x1": 3000, "y1": 3800, "x2": 3800, "y2": 4200}
}
```

## 坐标系统约定

- **默认单位**：mm（与CAD一致）
- **原点**：图纸左下角 `(0, 0)`
- **Y轴**：向上为正（与数学坐标系一致，与屏幕坐标系相反，转换时需注意）
- **bbox格式**：`{"x1": 左, "y1": 下, "x2": 右, "y2": 上}`

## Pydantic 模型骨架

后端必须严格对齐以上Schema：

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from decimal import Decimal

class BBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float

class Space(BaseModel):
    id: str
    name: str
    category: Literal["卧室","客厅","厨房","卫生间","楼梯间","电梯间","走廊","阳台","储藏","设备间","其他"]
    area: float = Field(gt=0)
    bbox: BBox
    windows: List[str] = []
    doors: List[str] = []
    height: Optional[float] = None
    natural_lighting: Optional[bool] = None

class DrawingJSON(BaseModel):
    version: str = "1.0.0"
    drawing_info: dict
    building_info: dict
    spaces: List[Space]
    walls: List[dict]
    doors: List[dict]
    windows: List[dict]
    stairs: List[dict] = []
    elevators: List[dict] = []
    annotations: List[dict] = []
    dimensions: List[dict] = []
    coordinate_system: dict
```
