import os
import time
import csv
from collections import defaultdict

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.lib import hub


class InstrumentedSimpleSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # MAC learning table: dpid -> {mac: port}
        self.mac_to_port = defaultdict(dict)

        # Output directory (one per run)
        self.outdir = os.environ.get("OUTDIR", "results/raw/devrun")
        os.makedirs(self.outdir, exist_ok=True)

        # Flow-event CSV: per Packet-In timing samples
        self.flow_events_path = os.path.join(self.outdir, "flow_events.csv")
        self.flow_events_f = open(self.flow_events_path, "w", newline="")
        self.flow_events_w = csv.writer(self.flow_events_f)
        self.flow_events_w.writerow([
            "ts_wall",
            "dpid",
            "src_mac",
            "dst_mac",
            "in_port",
            "proc_time_ms",
            "msg_len"
        ])
        self.flow_events_f.flush()

        # Per-second controller metrics CSV
        self.ctrl_metrics_path = os.path.join(self.outdir, "controller_metrics.csv")
        self.ctrl_metrics_f = open(self.ctrl_metrics_path, "w", newline="")
        self.ctrl_metrics_w = csv.writer(self.ctrl_metrics_f)
        self.ctrl_metrics_w.writerow([
            "ts_wall",
            "packet_in_count",
            "flow_mod_count"
        ])
        self.ctrl_metrics_f.flush()

        # Counters reset every second
        self.packet_in_count = 0
        self.flow_mod_count = 0

        # Start a background logger thread
        self._logger_thread = hub.spawn(self._per_second_logger)

    def _per_second_logger(self):
        while True:
            ts = time.time()
            self.ctrl_metrics_w.writerow([ts, self.packet_in_count, self.flow_mod_count])
            self.ctrl_metrics_f.flush()

            # reset counters for next second
            self.packet_in_count = 0
            self.flow_mod_count = 0
            hub.sleep(1.0)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # table-miss: send unknown packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    def _add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)

        datapath.send_msg(mod)
        self.flow_mod_count += 1

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        t0 = time.perf_counter()
        self.packet_in_count += 1

        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        dpid = datapath.id
        in_port = msg.match.get('in_port', -1)

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        dst = eth.dst
        src = eth.src

        # learn a mac address to avoid flooding next time.
        self.mac_to_port[dpid][src] = in_port

        # decide output port
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # install a flow for known destination to reduce controller load
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self._add_flow(datapath, 1, match, actions, msg.buffer_id)
                proc_ms = (time.perf_counter() - t0) * 1000.0
                self.flow_events_w.writerow([time.time(), dpid, src, dst, in_port, proc_ms, len(msg.data)])
                self.flow_events_f.flush()
                return
            else:
                self._add_flow(datapath, 1, match, actions)

        # send packet out
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=msg.buffer_id,
                                  in_port=in_port,
                                  actions=actions,
                                  data=data)
        datapath.send_msg(out)

        proc_ms = (time.perf_counter() - t0) * 1000.0
        self.flow_events_w.writerow([time.time(), dpid, src, dst, in_port, proc_ms, len(msg.data)])
        self.flow_events_f.flush()

