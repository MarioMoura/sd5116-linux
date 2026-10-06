# Ghidra headless script: decompile every function to C, dump to file.
# Jython 2.7 (Ghidra).
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from java.io import PrintWriter

ifc = DecompInterface()
ifc.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager()

pw = PrintWriter("/home/mario/onu/diag/hi_pie_decomp.c")
count = 0
for f in fm.getFunctions(True):
    res = ifc.decompileFunction(f, 90, monitor)
    pw.println("/* ===== %s @ %s ===== */" % (f.getName(), f.getEntryPoint()))
    if res is not None and res.decompileCompleted():
        pw.println(res.getDecompiledFunction().getC())
    else:
        em = res.getErrorMessage() if res is not None else "no result"
        pw.println("// DECOMPILE FAILED: " + str(em))
    pw.println("")
    count += 1
pw.close()
print("DECOMP_DONE functions=%d" % count)
