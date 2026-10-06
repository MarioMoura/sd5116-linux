// Ghidra headless: decompile every function in hi_bridge.ko to C.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.util.task.ConsoleTaskMonitor;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.FunctionManager;
import java.io.PrintWriter;

public class DecompKvif extends GhidraScript {
    public void run() throws Exception {
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        ConsoleTaskMonitor monitor = new ConsoleTaskMonitor();
        FunctionManager fm = currentProgram.getFunctionManager();
        PrintWriter pw = new PrintWriter("/home/mario/onu/diag/hi_kvif_decomp.c");
        int count = 0;
        FunctionIterator it = fm.getFunctions(true);
        while (it.hasNext()) {
            Function f = it.next();
            pw.println("/* ===== " + f.getName() + " @ " + f.getEntryPoint() + " ===== */");
            DecompileResults res = ifc.decompileFunction(f, 90, monitor);
            if (res != null && res.decompileCompleted())
                pw.println(res.getDecompiledFunction().getC());
            else
                pw.println("// FAILED: " + (res != null ? res.getErrorMessage() : "no result"));
            pw.println();
            count++;
        }
        pw.close();
        println("DECOMP_DONE functions=" + count);
    }
}
