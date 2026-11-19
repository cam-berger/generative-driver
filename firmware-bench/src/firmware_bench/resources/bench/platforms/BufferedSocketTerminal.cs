// Based on Renode's ServerSocketTerminal and BackendTerminal (MIT):
// Copyright (c) 2010-2026 Antmicro
// Copyright (c) 2011-2015 Realtime Embedded
using System;
using Antmicro.Migrant;
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Peripherals.UART;
using Antmicro.Renode.Time;
using Antmicro.Renode.Utilities;

namespace GenerativeDriver.Benchmark
{
    public static class BufferedTerminalExtensions
    {
        public static void CreateBufferedSocketTerminal(this Emulation emulation, int port, string name)
        {
            emulation.ExternalsManager.AddExternal(new BufferedSocketTerminal(port), name);
        }
    }
    [Transient]
    public sealed class BufferedSocketTerminal : IExternal, IConnectable<IUART>, IDisposable
    {
        public BufferedSocketTerminal(int port)
        {
            server = new SocketServerProvider(false, false, serverName: "Benchmark UART");
            server.BufferSize = 4096;
            server.DataBlockReceived += ReceiveBlock;
            server.Start(port);
        }
        public void AttachTo(IUART uart)
        {
            this.uart = uart;
            machine = uart.GetMachine();
            uart.CharReceived += server.SendByte;
        }
        public void DetachFrom(IUART uart)
        {
            uart.CharReceived -= server.SendByte;
            this.uart = null;
            machine = null;
        }
        private void ReceiveBlock(byte[] data)
        {
            var target = uart;
            var owner = machine;
            if(target == null || owner == null) { return; }
            var stamp = TimeDomainsManager.Instance.GetEffectiveVirtualTimeStamp();
            // Deliver a received block at one virtual timestamp. Per-byte scheduling
            // can split a host write across the firmware's inter-frame timer.
            // This software bridge does not model physical UART byte timing.
            owner.HandleTimeDomainEvent<byte[]>(bytes => {
                foreach(var value in bytes) { target.WriteChar(value); }
            }, data, stamp);
        }
        public void Dispose() { server.Stop(); }
        private IUART uart;
        private IMachine machine;
        private readonly SocketServerProvider server;
    }
}
