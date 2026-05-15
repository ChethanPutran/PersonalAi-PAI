import ast
import operator as op

# Built-in skills (these would be separate files in skills/)
def execute(expression: str) -> float:
    """Safe calculator skill"""
    operators = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv,
        ast.Pow: op.pow,
        ast.USub: op.neg,
        ast.UAdd: op.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            return operators[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in operators:
            return operators[type(node.op)](_eval(node.operand))
        raise ValueError("Unsupported expression")

    tree = ast.parse(expression, mode="eval")
    return float(_eval(tree.body))
