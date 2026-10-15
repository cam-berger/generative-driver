// Generic exporter for a program whose import parameters were already justified.
// Besides the decompilations it writes static navigation tables, each bounded in bytes, and navigation.json,
// which records every table's rows, the rows found and whether its bound truncated it.
// @category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressIterator;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.TreeSet;

public class ExportDecomp extends GhidraScript {
    private static final long DEFAULT_TABLE_BYTES = 16L << 20;
    private static final long MIN_TABLE_BYTES = 1024;
    private static final long MAX_TABLE_BYTES = 1L << 30;
    private static final int MAX_STRING_CHARS = 4096;
    private static final String NOTE = "Static analysis facts: a reference, a string use or a switch case does not "
        + "show that the code runs or is reachable at run time.";

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1 || args.length > 2 || !new File(args[0]).isAbsolute()) {
            throw new IllegalArgumentException("Provide one absolute workspace-local export directory "
                + "and optionally a byte bound for each navigation table");
        }
        long tableBytes = args.length == 2 ? Long.parseLong(args[1]) : DEFAULT_TABLE_BYTES;
        if (tableBytes < MIN_TABLE_BYTES || tableBytes > MAX_TABLE_BYTES) {
            throw new IllegalArgumentException("A navigation table bound must be from "
                + MIN_TABLE_BYTES + " to " + MAX_TABLE_BYTES + " bytes");
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
        List<Table> tables = new ArrayList<>();
        tables.add(exportReferences(output, tableBytes));
        tables.add(exportStrings(output, tableBytes));
        writeManifest(output, tableBytes, tables);
    }

    // Every memory reference, by source address: stack, register and external references are left out.
    private Table exportReferences(File output, long tableBytes) throws Exception {
        ReferenceManager manager = currentProgram.getReferenceManager();
        try (Table table = new Table(output, "references.tsv", tableBytes,
                "from_address", "from_function", "to_address", "to_function", "type")) {
            AddressIterator sources = manager.getReferenceSourceIterator(currentProgram.getMemory(), true);
            while (sources.hasNext()) {
                monitor.checkCancelled();
                Address from = sources.next();
                Reference[] references = manager.getReferencesFrom(from);
                Arrays.sort(references, Comparator.comparing(Reference::getToAddress)
                    .thenComparing(reference -> reference.getReferenceType().getName())
                    .thenComparingInt(Reference::getOperandIndex));
                for (Reference reference : references) {
                    if (reference.isMemoryReference()) {
                        table.row(from.toString(), entry(from), reference.getToAddress().toString(),
                            entry(reference.getToAddress()), reference.getReferenceType().getName());
                    }
                }
            }
            return table;
        }
    }

    // Every string Ghidra defined, with the functions containing a reference to any of its bytes and the
    // references from outside every function, such as pointer tables. A value longer than MAX_STRING_CHARS is
    // clipped to that many characters and marked; control characters and backslashes are escaped.
    private Table exportStrings(File output, long tableBytes) throws Exception {
        ReferenceManager manager = currentProgram.getReferenceManager();
        try (Table table = new Table(output, "strings.tsv", tableBytes,
                "address", "encoding", "length", "clipped", "functions", "refs_outside_functions", "value")) {
            for (Data data : currentProgram.getListing().getDefinedData(true)) {
                monitor.checkCancelled();
                if (!StringDataInstance.isString(data)) {
                    continue;
                }
                TreeSet<Address> functions = new TreeSet<>();
                TreeSet<Address> outside = new TreeSet<>();
                AddressIterator targets = manager.getReferenceDestinationIterator(
                    new AddressSet(data.getMinAddress(), data.getMaxAddress()), true);
                while (targets.hasNext()) {
                    for (Reference reference : manager.getReferencesTo(targets.next())) {
                        Function function = getFunctionContaining(reference.getFromAddress());
                        if (function != null) {
                            functions.add(function.getEntryPoint());
                        } else {
                            outside.add(reference.getFromAddress());
                        }
                    }
                }
                StringDataInstance string = StringDataInstance.getStringDataInstance(data);
                String value = string.getStringValue();
                String charset = string.getCharsetName();
                int length = value == null ? 0 : value.codePointCount(0, value.length());
                boolean clipped = length > MAX_STRING_CHARS;
                if (clipped) {
                    value = value.substring(0, value.offsetByCodePoints(0, MAX_STRING_CHARS));
                }
                table.row(data.getMinAddress().toString(), charset == null ? "unknown" : charset,
                    value == null ? "unknown" : Integer.toString(length), Boolean.toString(clipped),
                    join(functions), join(outside), value == null ? "" : escape(value));
            }
            return table;
        }
    }

    private static String join(TreeSet<Address> addresses) {
        StringBuilder text = new StringBuilder();
        for (Address address : addresses) {
            text.append(text.length() == 0 ? "" : ",").append(address);
        }
        return text.toString();
    }

    // One line per value: backslash, tab, newline and carriage return become \\, \t, \n and \r; other control
    // characters and U+2028/U+2029 become a backslash, the letter u and four hexadecimal digits.
    private static String escape(String value) {
        StringBuilder text = new StringBuilder(value.length());
        for (char c : value.toCharArray()) {
            if (c == '\\') {
                text.append("\\\\");
            } else if (c == '\t') {
                text.append("\\t");
            } else if (c == '\n') {
                text.append("\\n");
            } else if (c == '\r') {
                text.append("\\r");
            } else if (Character.isISOControl(c) || c == '\u2028' || c == '\u2029') {
                text.append(String.format("\\u%04x", (int) c));
            } else {
                text.append(c);
            }
        }
        return text.toString();
    }

    // The entry address of the function containing `address`, or empty when no function does.
    private String entry(Address address) {
        Function function = getFunctionContaining(address);
        return function == null ? "" : function.getEntryPoint().toString();
    }

    private void writeManifest(File output, long tableBytes, List<Table> tables) throws IOException {
        StringBuilder json = new StringBuilder("{\n  \"schema\": \"ghidra-navigation/1\",\n  \"max_bytes\": ")
            .append(tableBytes).append(",\n  \"note\": \"").append(NOTE).append("\",\n  \"tables\": {");
        for (int i = 0; i < tables.size(); i++) {
            Table table = tables.get(i);
            json.append(i == 0 ? "\n" : ",\n").append("    \"").append(table.name).append("\": {\"rows\": ")
                .append(table.rows).append(", \"rows_found\": ").append(table.rowsFound)
                .append(", \"truncated\": ").append(table.rowsFound > table.rows).append('}');
        }
        json.append("\n  }\n}\n");
        try (Writer manifest = utf8(new File(output, "navigation.json"))) {
            manifest.write(json.toString());
        }
    }

    private PrintWriter writer(File file) throws Exception {
        return new PrintWriter(file, StandardCharsets.UTF_8.name());
    }

    private static Writer utf8(File file) throws IOException {
        return new BufferedWriter(new OutputStreamWriter(new FileOutputStream(file), StandardCharsets.UTF_8));
    }

    // Tab-separated rows ending in a newline. Once a row would take the file past its bound, that row and every
    // later one are counted but not written, so the file holds the leading rows.
    private static final class Table implements AutoCloseable {
        final String name;
        final long maxBytes;
        final Writer out;
        long bytes;
        long rows;
        long rowsFound;

        Table(File directory, String name, long maxBytes, String... columns) throws IOException {
            this.name = name;
            this.maxBytes = maxBytes;
            out = utf8(new File(directory, name));
            String header = String.join("\t", columns) + "\n";
            out.write(header);
            bytes = header.getBytes(StandardCharsets.UTF_8).length;
        }

        void row(String... fields) throws IOException {
            rowsFound++;
            String line = String.join("\t", fields) + "\n";
            long size = line.getBytes(StandardCharsets.UTF_8).length;
            if (rows + 1 == rowsFound && bytes + size <= maxBytes) {
                out.write(line);
                bytes += size;
                rows++;
            }
        }

        @Override
        public void close() throws IOException {
            out.close();
        }
    }
}
