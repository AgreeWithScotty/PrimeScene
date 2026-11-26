// Actual authorized CDM decoder, not a substitute software decoder.
#pragma once
#include "mojo/public/c/system/data_pipe.h"
class DecoderProbe {
 public:
  std::vector<uint8_t> initialize_message;
  std::vector<uint8_t> sample_message,sample_data;
  bool requested=false,initialized=false,failed=false;
  bool sample_sent=false,eos_sent=false,decode_finished=false;
  uint32_t decoded_frames=0;
  std::vector<MojoHandle> owned_data_pipes;
  ~DecoderProbe(){for(auto handle:owned_data_pipes)MojoClose(handle);}
  void Begin(MojoHandle decryptor) {
    requested=true;
    if(initialize_message.size()<48||U32(initialize_message.data()+12)!=kDecryptorInitializeVideo) {
      Event("actual_hd_decoder_configuration","invalid");failed=true;return;
    }
    // Initialize constructs Chrome's per-stream decoder objects. It must
    // precede decoder initialization as well as buffer-transfer operations.
    std::vector<MojoHandle> remote;
    for(unsigned i=0;i<4;++i) {
      MojoHandle producer=MOJO_HANDLE_INVALID,consumer=MOJO_HANDLE_INVALID;
      MojoCreateDataPipeOptions options={sizeof(options),0,1,4*1024*1024};
      auto rc=MojoCreateDataPipe(&options,&producer,&consumer);
      if(rc!=MOJO_RESULT_OK){for(auto h:remote)MojoClose(h);Event("actual_decoder_data_pipe","failure",rc);failed=true;return;}
      owned_data_pipes.push_back(i==3?consumer:producer);remote.push_back(i==3?producer:consumer);
    }
    auto setup=Header(kDecryptorInitialize,0,0,56);Put32(setup.data()+32,24);
    for(unsigned i=0;i<4;++i)Put32(setup.data()+40+i*4,i);
    bool ok=SendBytes(decryptor,setup,remote);
    Event("actual_decoder_data_pipes_initialize",ok?"sent":"failure");
    if(!ok){failed=true;return;}
    ok=SendBytes(decryptor,initialize_message);
    Event("actual_chrome_hd_decoder_initialize",ok?"sent":"failure");if(!ok)failed=true;
  }
  void Tick(MojoHandle decryptor) {
    if(failed||decode_finished)return;
    if(initialized&&!sample_message.empty()&&!sample_sent) {
      uint32_t size=static_cast<uint32_t>(sample_data.size());
      MojoWriteDataOptions options={sizeof(options),MOJO_WRITE_DATA_FLAG_ALL_OR_NONE};
      auto rc=MojoWriteData(owned_data_pipes[1],sample_data.data(),&size,&options);
      if(rc!=MOJO_RESULT_OK||size!=sample_data.size()){Event("actual_encrypted_video_pipe","failure",rc);failed=true;return;}
      bool ok=SendBytes(decryptor,sample_message);sample_sent=true;
      Event("actual_encrypted_hd_frame_decode",ok?"sent":"failure",size);
      if(!ok){failed=true;return;}
    }
    if(initialized&&!sample_sent)return;
    WireMessage message;auto rc=ReadWire(decryptor,message);
    if(rc==MOJO_RESULT_SHOULD_WAIT)return;
    if(rc!=MOJO_RESULT_OK){Event("actual_hd_decoder_pipe","failure",rc);failed=true;return;}
    auto body=Payload(message.bytes);
    if(body==UINT32_MAX||message.bytes.size()-body<16||U32(message.bytes.data()+16)!=2) {
      Event("actual_chrome_hd_decoder_initialize","invalid_response");failed=true;return;
    }
    if(!initialized) {
      if(U32(message.bytes.data()+12)!=kDecryptorInitializeVideo||U64(message.bytes.data()+24)!=61){failed=true;return;}
      initialized=message.bytes[body+8]!=0;
      Event("actual_chrome_hd_decoder_initialize",initialized?"accepted":"rejected");
      if(!initialized)failed=true;
    } else {
      if(U32(message.bytes.data()+12)!=kDecryptorDecodeVideo||message.bytes.size()-body<32){failed=true;return;}
      Event("actual_encrypted_hd_decode_status","observed",U32(message.bytes.data()+body+8));
      uint64_t delta=U64(message.bytes.data()+body+16);
      if(delta&&delta<=message.bytes.size()-body-16) {
        uint64_t frame=body+16+delta;
        if(message.bytes.size()-frame>=88) {
          uint64_t size_delta=U64(message.bytes.data()+frame+16);
          if(size_delta&&size_delta<=message.bytes.size()-frame-16) {
            uint64_t size=frame+16+size_delta;
            if(message.bytes.size()-size>=16) {
              uint32_t width=U32(message.bytes.data()+size+8),height=U32(message.bytes.data()+size+12);
              if(width&&height) {
                ++decoded_frames;
                Event("actual_decoded_video_width","observed",width);
                Event("actual_decoded_video_height","observed",height);
                Event("actual_decoded_video_format","observed",U32(message.bytes.data()+frame+8));
                Event("actual_decoded_frame_handles","count",message.handles.size());
              }
            }
          }
        }
      }
      if(!eos_sent) {
        auto eos=Header(kDecryptorDecodeVideo,1,63,80);Put32(eos.data()+32,24);Put32(eos.data()+40,16);
        Put64(eos.data()+48,8);Put32(eos.data()+56,24);
        bool ok=SendBytes(decryptor,eos);eos_sent=true;
        Event("actual_decoder_drain",ok?"sent":"failure");if(!ok)failed=true;
      } else {decode_finished=true;Event("actual_hd_decoded_frames","count",decoded_frames);}
    }
  }
};
