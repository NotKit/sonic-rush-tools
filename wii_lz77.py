# From http://wiibrew.org/wiki/LZ77
import sys, struct
 
class WiiLZ77:
    TYPE_LZ77 = 1
    def __init__(self, file, offset):
        self.file = file
        self.offset = offset
 
        self.file.seek(self.offset)
 
        hdr = struct.unpack("<I",self.file.read(4))[0]
        self.uncompressed_length = hdr>>8
        self.compression_type = hdr>>4 & 0xF
 
        if self.compression_type != self.TYPE_LZ77:
            raise ValueError("Unsupported compression method %d"%self.compression_type)
 
    def uncompress(self):
        dout = ""
 
        self.file.seek(self.offset + 0x4)
 
        while len(dout) < self.uncompressed_length:
            flags = struct.unpack("<B",self.file.read(1))[0]
 
            for i in range(8):
                if flags & 0x80:
                    info = struct.unpack(">H",self.file.read(2))[0]
                    num = 3 + ((info>>12)&0xF)
                    disp = info & 0xFFF
                    ptr = len(dout) - (info & 0xFFF) - 1
                    for i in range(num):
                        dout += dout[ptr]
                        ptr+=1
                        if len(dout) >= self.uncompressed_length:
                            break
                else:
                    dout += self.file.read(1)
                flags <<= 1
                if len(dout) >= self.uncompressed_length:
                    break
 
        self.data = dout
        return self.data