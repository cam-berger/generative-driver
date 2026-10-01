// Seed a Cortex-M raw image from its own reset vector, without external symbols.
// @category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.lang.Register;
import java.math.BigInteger;

public class SeedCortexM extends GhidraScript {
    @Override
    public void run() throws Exception {
        Address base = currentProgram.getMinAddress();
        long vector = Integer.toUnsignedLong(currentProgram.getMemory().getInt(base.add(4)));
        if ((vector & 1) != 1) {
            throw new IllegalArgumentException("Reset vector must select Thumb mode");
        }
        Address entry = base.getAddressSpace().getAddress(vector & ~1L);
        MemoryBlock block = currentProgram.getMemory().getBlock(entry);
        if (block == null || !block.isInitialized() || !block.isExecute()
                || !block.contains(entry.add(1))) {
            throw new IllegalArgumentException("Reset vector must address mapped executable bytes");
        }
        Register thumb = currentProgram.getRegister("TMode");
        if (thumb == null) {
            throw new IllegalArgumentException("Cortex-M Thumb register unavailable");
        }
        currentProgram.getProgramContext().setValue(thumb, entry, entry, BigInteger.ONE);
        addEntryPoint(entry);
        if (!disassemble(entry) || createFunction(entry, null) == null) {
            throw new IllegalStateException("Reset vector did not yield an executable entry");
        }
    }
}
