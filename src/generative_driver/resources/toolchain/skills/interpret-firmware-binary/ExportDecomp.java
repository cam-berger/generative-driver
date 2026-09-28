// Generic exporter for a program whose import parameters were already justified.
// @category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import java.io.File;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

public class ExportDecomp extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1 || !new File(args[0]).isAbsolute()) {
            throw new IllegalArgumentException("Provide one absolute workspace-local export directory");
        }
        File output = new File(args[0]);
        if (!output.isDirectory() && !output.mkdirs()) {
            throw new IllegalStateException("Cannot create export directory");
        }
        DecompInterface decompiler = new DecompInterface();
        try {
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException("Cannot open imported program for decompilation");
            }
            try (PrintWriter inventory = writer(new File(output, "functions.tsv"))) {
                inventory.println("address\tname\tdecompilation_complete");
                FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
                while (functions.hasNext() && !monitor.isCancelled()) {
                    Function function = functions.next();
                    String address = function.getEntryPoint().toString();
                    DecompileResults result = decompiler.decompileFunction(function, 60, monitor);
                    inventory.println(address + "\t" + function.getName() + "\t" + result.decompileCompleted());
                    String filename = "function_" + address.replaceAll("[^A-Za-z0-9_-]", "_") + ".c";
                    try (PrintWriter text = writer(new File(output, filename))) {
                        text.println("// entry address: " + address);
                        if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                            text.println(result.getDecompiledFunction().getC());
                        } else {
                            text.println("// DECOMPILATION FAILED: " + result.getErrorMessage());
                        }
                    }
                }
            }
            try (PrintWriter listing = writer(new File(output, "disassembly.txt"))) {
                InstructionIterator instructions = currentProgram.getListing().getInstructions(true);
                while (instructions.hasNext() && !monitor.isCancelled()) {
                    Instruction instruction = instructions.next();
                    listing.println(instruction.getAddress() + "\t" + instruction.toString());
                }
            }
        } finally {
            decompiler.dispose();
        }
    }

    private PrintWriter writer(File file) throws Exception {
        return new PrintWriter(file, StandardCharsets.UTF_8.name());
    }
}
