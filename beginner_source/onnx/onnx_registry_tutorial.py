"""
`Introduction to ONNX <intro_onnx.html>`_ ||
`Exporting a PyTorch model to ONNX <export_simple_model_to_onnx_tutorial.html>`_ ||
**Extending the ONNX Exporter Operator Support**

Extending the ONNX Exporter Operator Support
============================================

**Authors:** Ti-Tai Wang (titaiwang@microsoft.com), Justin Chu (justinchu@microsoft.com)
"""


###############################################################################
# Overview
# --------
#
# This tutorial describes how you can create ONNX implementation for unsupported PyTorch operators
# or replace existing implementation with your own.
#
# We will cover three scenarios that require extending the ONNX exporter's operator support:
#
# * Overriding the implementation of an existing PyTorch operator
# * Using custom ONNX operators
# * Supporting a custom PyTorch operator
#
# Overriding the implementation of an existing PyTorch operator
# -------------------------------------------------------------
#
# Although the ONNX exporter team does their best efforts to support all PyTorch operators, some of them
# might not be supported yet. In this section, we will demonstrate how you can add
# unsupported PyTorch operators to the ONNX Registry.
#
# .. note::
#       The steps to implement unsupported PyTorch operators are the same to replace the implementation of an existing
#       PyTorch operator with a custom implementation.
#       Because we don't actually have an unsupported PyTorch operator to use in this tutorial, we are going to leverage
#       this and replace the implementation of ``torch.ops.aten.add.Tensor`` with a custom implementation the same way we would
#       if the operator was not implemented by the ONNX exporter.
#
# When a model cannot be exported to ONNX due to an unsupported operator, the ONNX exporter will show an error message
# similar to:
#
# .. code-block:: python
#
#   No decompositions registered for [...]
#
# The error message indicates that the unsupported PyTorch operator is ``torch.ops.aten.add.Tensor``.
# The operator is of type ``<class 'torch._ops.OpOverload'>``, and this operator is what we will use as the
# target to register our custom implementation.
#
# To add support for an unsupported PyTorch operator or to replace the implementation for an existing one, we need:
#
# * The target PyTorch operator.
# * The implementation of the operator using `ONNX Script <https://github.com/microsoft/onnxscript>`__.
#   ONNX Script is a prerequisite for this tutorial. Please make sure you have read the
#   `ONNX Script tutorial <https://github.com/microsoft/onnxscript/blob/main/docs/tutorial/index.md>`_
#   before proceeding.

import torch
import onnxruntime
import onnxscript

# Opset 18 is the standard supported version as of PyTorch 2.6
from onnxscript import opset18 as op


# Create a model that uses the operator torch.ops.aten.add.Tensor
class Model(torch.nn.Module):
    def forward(self, input_x, input_y):
        return torch.ops.aten.add.Tensor(input_x, input_y)


# NOTE: The function signature (including param names) must match the signature of the unsupported PyTorch operator.
# https://github.com/pytorch/pytorch/blob/main/aten/src/ATen/native/native_functions.yaml
# NOTE: All attributes must be annotated with type hints.
def custom_aten_add(self, other, alpha: float = 1.0):
    if alpha != 1.0:
        alpha = op.CastLike(alpha, other)
        other = op.Mul(other, alpha)
    # To distinguish the custom implementation from the builtin one, we switch the order of the inputs
    return op.Add(other, self)


x = torch.tensor([1.0])
y = torch.tensor([2.0])

# Then we provide the custom implementation to the ONNX exporter as a ``custom_translation_table``.
onnx_program = torch.onnx.export(
    Model().eval(),
    (x, y),
    dynamo=True,
    custom_translation_table={
        torch.ops.aten.add.Tensor: custom_aten_add,
    },
)
# Optimize the ONNX graph to remove redundant nodes
onnx_program.optimize()

######################################################################
# Now let's inspect the model and verify the model is using the custom implementation.

print(onnx_program.model)

######################################################################
# We get
#
# .. code-block:: python
#     <
#         ir_version=10,
#         opset_imports={'pkg.onnxscript.torch_lib.common': 1, '': 18},
#         producer_name='pytorch',
#         producer_version='2.7.0.dev20250124+cu124',
#         domain=None,
#         model_version=None,
#     >
#     graph(
#         name=main_graph,
#         inputs=(
#             %"input_x"<FLOAT,[1]>,
#             %"input_y"<FLOAT,[1]>
#         ),
#         outputs=(
#             %"add"<FLOAT,[1]>
#         ),
#     ) {
#         0 |  # node_Add_0
#             %"add"<FLOAT,[1]> ⬅️ ::Add(%"input_y", %"input_x")
#         return %"add"<FLOAT,[1]>
#     }
#
# The translation is using our custom implementation: In node ``node_Add_0``, ``input_y`` now
# comes first, and ``input_x`` comes second.
#
# We can use ONNX Runtime to run the model and verify the results by calling
# the ONNXProgram directly on the input tensors.

result = onnx_program(x, y)[0]
torch.testing.assert_close(result, torch.tensor([3.0]))


######################################################################
# Using custom ONNX operators
# ---------------------------
#
# In this case, you create a model with standard PyTorch operators, but the runtime
# (e.g. Microsoft's ONNX Runtime) can provide a custom implementation for that kernel, effectively replacing the
# existing implementation.
#
# In the following example, we use the ``com.microsoft.Gelu`` operator provided by ONNX Runtime,
# which is not the same ``Gelu`` from ONNX spec.


class GeluModel(torch.nn.Module):
    def forward(self, input_x):
        return torch.ops.aten.gelu(input_x)


# Create a namespace for the custom operator using ONNX Script
# com.microsoft is an official ONNX Runtime namespace
microsoft_op = onnxscript.values.Opset(domain="com.microsoft", version=1)

# NOTE: The function signature (including param names) must match the signature of the unsupported PyTorch operator.
# https://github.com/pytorch/pytorch/blob/main/aten/src/ATen/native/native_functions.yaml
# NOTE: All attributes must be annotated with type hints.
# The function must be scripted using the ``@onnxscript.script()`` decorator when
# using operators from custom domains. This may be improved in future versions.
from onnxscript import FLOAT


@onnxscript.script(microsoft_op)
def custom_aten_gelu(self: FLOAT, approximate: str = "none") -> FLOAT:
    return microsoft_op.Gelu(self)


onnx_program = torch.onnx.export(
    GeluModel().eval(),
    (x,),
    dynamo=True,
    custom_translation_table={
        torch.ops.aten.gelu.default: custom_aten_gelu,
    },
)

# Optimize the ONNX graph to remove redundant nodes
onnx_program.optimize()


######################################################################
# Let's inspect the model and verify the model uses op_type ``Gelu``
# from namespace ``com.microsoft``.
#

print(onnx_program.model)

######################################################################
# We get
#
# .. code-block:: python
#     <
#         ir_version=10,
#         opset_imports={'pkg.onnxscript.torch_lib.common': 1, 'com.microsoft': 1, '': 18},
#         producer_name='pytorch',
#         producer_version='2.7.0.dev20250124+cu124',
#         domain=None,
#         model_version=None,
#     >
#     graph(
#         name=main_graph,
#         inputs=(
#             %"input_x"<FLOAT,[1]>
#         ),
#         outputs=(
#             %"gelu"<FLOAT,[1]>
#         ),
#     ) {
#         0 |  # n0
#              %"gelu"<FLOAT,[1]> ⬅️ com.microsoft::Gelu(%"input_x")
#         return %"gelu"<FLOAT,[1]>
#     }


######################################################################
# Similar to the previous example, we can use ONNX Runtime to run the model and verify the results.

result = onnx_program(x)[0]
torch.testing.assert_close(result, torch.ops.aten.gelu(x))


######################################################################
# Supporting a custom PyTorch operator
# ------------------------------------
#
# In this case, the operator is an operator that is user implemented and registered to PyTorch.
#
# In the following example, we would like to use a custom operator
# that takes one tensor input, and returns one output. The operator adds
# the input to itself, and returns the rounded result.
#
# Firstly, we assume the custom operator is implemented and registered with ``torch.library.custom_op()``.
# You can refer to `Creating new custom ops in Python <https://pytorch.org/docs/stable/library.html#torch.library.custom_op>`_
# for a detailed guide on how to create custom operators.


# Define and use the operator in PyTorch
@torch.library.custom_op("mylibrary::add_and_round_op", mutates_args=())
def add_and_round_op(input: torch.Tensor) -> torch.Tensor:
    return torch.round(input + input)


@add_and_round_op.register_fake
def _add_and_round_op_fake(tensor_x):
    return torch.empty_like(tensor_x)


class AddAndRoundModel(torch.nn.Module):
    def forward(self, input):
        return add_and_round_op(input)


# Implement the custom operator in ONNX using ONNX Script
def onnx_add_and_round(input):
    return op.Round(op.Add(input, input))


onnx_program = torch.onnx.export(
    AddAndRoundModel().eval(),
    (x,),
    dynamo=True,
    custom_translation_table={
        torch.ops.mylibrary.add_and_round_op.default: onnx_add_and_round,
    },
)

# Optimize the ONNX graph to remove redundant nodes
onnx_program.optimize()
print(onnx_program)

######################################################################
# We get
#
# .. code-block:: python
#     <
#         ir_version=10,
#         opset_imports={'pkg.onnxscript.torch_lib.common': 1, '': 18},
#         producer_name='pytorch',
#         producer_version='2.7.0.dev20250124+cu124',
#         domain=None,
#         model_version=None,
#     >
#     graph(
#         name=main_graph,
#         inputs=(
#             %"input"<FLOAT,[1]>
#         ),
#         outputs=(
#             %"add_and_round_op"<FLOAT,[1]>
#         ),
#     ) {
#         0 |  # node_Add_0
#             %"val_0"<FLOAT,[1]> ⬅️ ::Add(%"input", %"input")
#         1 |  # node_Round_1
#             %"add_and_round_op"<FLOAT,[1]> ⬅️ ::Round(%"val_0")
#         return %"add_and_round_op"<FLOAT,[1]>
#     }
#
# And exported program
#
# .. code-block:: python
#     ExportedProgram:
#         class GraphModule(torch.nn.Module):
#             def forward(self, input: "f32[1]"):
#                 input_1 = input
#
#                 add_and_round_op: "f32[1]" = torch.ops.mylibrary.add_and_round_op.default(input_1);  input_1 = None
#                 return (add_and_round_op,)
#
#     Graph signature: ExportGraphSignature(input_specs=[InputSpec(kind=<InputKind.USER_INPUT: 1>, arg=TensorArgument(name='input'), target=None, persistent=None)], output_specs=[OutputSpec(kind=<OutputKind.USER_OUTPUT: 1>, arg=TensorArgument(name='add_and_round_op'), target=None)])
#     Range constraints: {}
#
# The translation is using our custom implementation to translate the ``torch.ops.mylibrary.add_and_round_op.default``
# operator in the ExportedProgram to the ONNX operator ``Add`` and ``Round``.
#

######################################################################
# Finally we verify the results.

result = onnx_program(x)[0]
torch.testing.assert_close(result, add_and_round_op(x))

######################################################################
# Conclusion
# ----------
#
# Congratulations! In this tutorial, we explored the ``custom_translation_table`` option and
# discovered how to create custom implementations for unsupported or existing PyTorch operators
# using ONNX Script.
#
# Finally, we leveraged ONNX Runtime to execute the model and compare the results with PyTorch,
# providing us with a comprehensive understanding of handling unsupported
# operators in the ONNX ecosystem.
#
# Further reading
# ---------------
#
# The list below refers to tutorials that ranges from basic examples to advanced scenarios,
# not necessarily in the order they are listed.
# Feel free to jump directly to specific topics of your interest or
# sit tight and have fun going through all of them to learn all there is about the ONNX exporter.
#
# .. include:: /beginner_source/onnx/onnx_toc.txt
#
# .. toctree::
#    :hidden:
#
