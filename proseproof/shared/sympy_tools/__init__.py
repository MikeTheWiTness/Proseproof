from .safety import check_dangerous
from .sandbox import execute_code
from .templates import build_code
from .tools import (
    EvaluateExpressionTool,
    SolveEquationTool,
    CheckEqualityTool,
    SimplifyExpressionTool,
    SolvePhysicsFormulaTool,
    DimensionalAnalysisTool,
    ComputeLimitTool,
    GeometryTool,
    VectorOperationsTool,
    CircleFromTwoPointsTool,
    BalanceChemicalEquationTool,
    StoichiometryCalcTool,
)

ALL_TOOLS = [
    EvaluateExpressionTool(),
    SolveEquationTool(),
    CheckEqualityTool(),
    SimplifyExpressionTool(),
    SolvePhysicsFormulaTool(),
    DimensionalAnalysisTool(),
    ComputeLimitTool(),
    GeometryTool(),
    VectorOperationsTool(),
    CircleFromTwoPointsTool(),
    BalanceChemicalEquationTool(),
    StoichiometryCalcTool(),
]


def get_tools_for_langgraph():
    return ALL_TOOLS
