# 审图规则引擎设计规范与示例

## 设计原则

1. **规则即配置**：每条规范条文对应一个Python类，禁止硬编码在API或Service中
2. **可插拔**：规则引擎自动扫描`rules/`目录加载所有规则，新增规范只需新增文件
3. ** severity分级**：`error`（强条，必须改）、`warning`（推荐优化）、`info`（提示）
4. **上下文感知**：规则接收完整的DrawingJSON，可跨元素关联检查（如"楼梯间窗户与卧室窗户的距离"）
5. **可解释性**：每条违规必须输出`reason`（原因）和`suggestion`（修改建议）

## 规则基类

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from enum import Enum

class Severity(str, Enum):
    ERROR = "error"       # 违反强条，必须修改
    WARNING = "warning"   # 不符合推荐做法
    INFO = "info"         # 提醒/提示

class Violation(BaseModel):
    rule_id: str           # 规范编号，如 "GB50016-6.2.5"
    rule_name: str         # 规范名称
    severity: Severity
    element_ids: List[str] # 涉及的图纸元素ID
    element_type: str      # "room" / "window" / "door" / "building"
    message: str           # 简短违规描述
    reason: str            # 引用的规范条文原文或解释
    suggestion: str        # 具体修改建议
    bbox: Optional[Dict[str, float]] = None  # 违规位置，用于前端高亮

class BaseRule(ABC):
    """所有审图规则的基类"""
    
    rule_id: str = ""           # 规范编号
    rule_name: str = ""         # 规范名称
    description: str = ""       # 规则描述
    applicable_types: List[str] = []  # 适用建筑类型
    
    @abstractmethod
    def check(self, drawing: DrawingJSON) -> List[Violation]:
        """执行审查，返回违规列表（空列表表示通过）"""
        pass
    
    def is_applicable(self, drawing: DrawingJSON) -> bool:
        """判断本规则是否适用于当前图纸"""
        if not self.applicable_types:
            return True
        return drawing.building_info.building_type in self.applicable_types
```

## 示例规则 1：住宅卧室最小面积

**规范来源**：《住宅设计规范》GB 50096-2011 第5.2.1条

```python
class BedroomMinAreaRule(BaseRule):
    """
    规范：双人卧室不应小于9㎡，单人卧室不应小于5㎡。
    此处简化为：所有卧室（category="卧室"）面积 >= 9㎡。
    """
    rule_id = "GB50096-5.2.1"
    rule_name = "卧室最小使用面积"
    description = "双人卧室不应小于9㎡"
    applicable_types = ["住宅"]
    
    MIN_AREA = 9.0  # ㎡
    
    def check(self, drawing: DrawingJSON) -> List[Violation]:
        violations = []
        for space in drawing.spaces:
            if space.category != "卧室":
                continue
            if space.area < self.MIN_AREA:
                violations.append(Violation(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=Severity.ERROR,
                    element_ids=[space.id],
                    element_type="room",
                    message=f"卧室'{space.name}'面积{space.area}㎡，小于规范最小值{self.MIN_AREA}㎡",
                    reason="《住宅设计规范》GB 50096-2011 第5.2.1条规定：双人卧室不应小于9㎡",
                    suggestion=f"建议扩大该卧室尺寸，确保使用面积不小于{self.MIN_AREA}㎡；或调整功能为书房/储藏间",
                    bbox=space.bbox.model_dump() if space.bbox else None
                ))
        return violations
```

## 示例规则 2：超高层住宅外窗耐火完整性

**规范来源**：《建筑设计防火规范》GB 50016-2014（2018年版）第6.2.5条

```python
class WindowFireResistanceRule(BaseRule):
    """
    规范：建筑高度大于54m的住宅建筑，外窗耐火完整性不应低于1.00h。
    """
    rule_id = "GB50016-6.2.5"
    rule_name = "超高层住宅外窗耐火完整性"
    description = "建筑高度>54m时，外窗耐火完整性≥1.00h"
    applicable_types = ["住宅"]
    
    HEIGHT_THRESHOLD = 54.0  # m
    MIN_FIRE_RESISTANCE = 1.0  # h
    
    def check(self, drawing: DrawingJSON) -> List[Violation]:
        violations = []
        height = drawing.building_info.building_height
        
        if height <= self.HEIGHT_THRESHOLD:
            return violations  # 不适用
        
        for window in drawing.windows:
            # 外窗判断逻辑：所属房间有自然采光且不是走廊/楼梯间等公共区
            room = next((s for s in drawing.spaces if s.id == window.room_id), None)
            if not room:
                continue
            if room.category in ["走廊", "楼梯间", "电梯间"]:
                continue  # 内走廊窗不审查
                
            if (window.fire_resistance_hours or 0) < self.MIN_FIRE_RESISTANCE:
                violations.append(Violation(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=Severity.ERROR,
                    element_ids=[window.id, room.id],
                    element_type="window",
                    message=f"外窗'{window.id}'（{room.name}）耐火完整性{window.fire_resistance_hours or 0}h不足",
                    reason=f"建筑高度{height}m>{self.HEIGHT_THRESHOLD}m，《建防火规》GB50016-6.2.5要求外窗耐火完整性≥{self.MIN_FIRE_RESISTANCE}h",
                    suggestion=f"将该外窗改为耐火完整性不低于{self.MIN_FIRE_RESISTANCE}h的防火窗（如断桥铝合金防火窗+防火玻璃）",
                    bbox=window.bbox.model_dump() if window.bbox else None
                ))
        return violations
```

## 示例规则 3：卫生间自然采光（推荐性条文）

**规范来源**：《住宅设计规范》GB 50096-2011 第7.1.3条（推荐性）

```python
class BathroomNaturalLightingRule(BaseRule):
    """
    规范：卫生间宜有直接采光、自然通风。
    "宜"字表明是推荐性条文，标记为warning而非error。
    """
    rule_id = "GB50096-7.1.3"
    rule_name = "卫生间自然采光与通风"
    description = "卫生间宜有直接采光、自然通风"
    applicable_types = ["住宅"]
    
    def check(self, drawing: DrawingJSON) -> List[Violation]:
        violations = []
        for space in drawing.spaces:
            if space.category != "卫生间":
                continue
            if not space.natural_lighting:
                violations.append(Violation(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=Severity.WARNING,  # 推荐性条文
                    element_ids=[space.id],
                    element_type="room",
                    message=f"卫生间'{space.name}'无直接自然采光",
                    reason="《住宅设计规范》GB 50096-2011 第7.1.3条：卫生间宜有直接采光、自然通风",
                    suggestion="建议增设外窗或采光井；若条件受限，应加强机械通风设计并满足换气次数要求",
                    bbox=space.bbox.model_dump() if space.bbox else None
                ))
        return violations
```

## 规则引擎主入口

```python
class RuleEngine:
    def __init__(self):
        self.rules: List[BaseRule] = self._load_rules()
    
    def _load_rules(self) -> List[BaseRule]:
        """自动扫描rules/目录加载所有规则类"""
        # 实际实现可用importlib扫描模块
        return [
            BedroomMinAreaRule(),
            WindowFireResistanceRule(),
            BathroomNaturalLightingRule(),
            # ... 更多规则
        ]
    
    def review(self, drawing: DrawingJSON) -> List[Violation]:
        """执行完整审图，返回所有违规"""
        all_violations = []
        for rule in self.rules:
            if not rule.is_applicable(drawing):
                continue
            try:
                violations = rule.check(drawing)
                all_violations.extend(violations)
            except Exception as e:
                # 单条规则失败不应阻断整体审图
                logger.error(f"规则 {rule.rule_id} 执行失败: {e}")
        return all_violations
```

## 新增规则 Checklist

当用户要求新增一条规范审查时，按此清单执行：

1. [ ] 确认规范条文原文（编号、名称、具体数值）
2. [ ] 判断是`error`还是`warning`（带"应/不得"的是error，带"宜/不宜"的是warning）
3. [ ] 确认适用范围（住宅/公建/工业？高度限制？）
4. [ ] 确认DrawingJSON中已有足够字段支持该判断，如不够则先改Schema
5. [ ] 编写Rule类，继承`BaseRule`
6. [ ] 编写测试用例：至少一个"应通过"和一个"应违规"的JSON输入
7. [ ] 在`RuleEngine._load_rules()`中注册
