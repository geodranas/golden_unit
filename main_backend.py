import time
import pandas as pd
from helper import Helper
import gparams
from orchestrator import Orchestrator
import socket
import time
import subprocess
import os
import pyshark
import json
from icmplib import ping, multiping, traceroute, resolve
from icmplib import async_ping, async_multiping, async_resolve
from icmplib import ICMPv4Socket, ICMPv6Socket, AsyncSocket, ICMPRequest, ICMPReply
from icmplib import ICMPLibError, NameLookupError, ICMPSocketError
from icmplib import SocketAddressError, SocketPermissionError
from icmplib import SocketUnavailableError, SocketBroadcastError, TimeoutExceeded
from icmplib import ICMPError, DestinationUnreachable, TimeExceeded
from io import StringIO

class Backend:
	def __init__(self):
		# help functions and params
		self.helper = Helper()
		self.counter_exp=0
		self.counter_camp=0
		self.df_out_monitor=None

		# init functions
		res=self.init_dbs()
		if res is None:
			return

		# read user input
		res=self.read_input()
		if res is None:
			return

		# run campaign
		self.db_in_user = res # python translates str booleans to Python types
		self.run_campaign()

	def init_dbs(self):

		mydbs=[
			gparams._DB_FILE_LOC_OUTPUT_BASE,
			gparams._DB_FILE_LOC_OUTPUT_LOG,
			gparams._RES_FILE_LOC_TWAMP,
			gparams._RES_FILE_LOC_OWAMP,
			gparams._RES_FILE_LOC_UDPPING,
			gparams._RES_FILE_LOC_ICMP,
			gparams._RES_FILE_LOC_IPERF,
			gparams._RES_FILE_LOC_PHY,
			gparams._RES_FILE_LOC_APP
		]

		if gparams._LOCAL_TEST:
			pass
		else:
			mydbs.append(gparams._DB_FILE_LOC_INPUT_USER)


		for el in mydbs:
			res=self.helper.init_db(loc=el,header=None)
			if res is None:
				return None

		return 200

	def read_input(self):
		res=None
		attempt = 1
		while (res is None):
			print('(Backend) DBG: Reading input sources (attempt='+str(attempt)+')...')

			if attempt>1:
				self.helper.wait(gparams._WAIT_SEC_BACKEND_READ_INPUT_SOURCES)

			res=self.helper.read_json2dict(loc=gparams._DB_FILE_LOC_INPUT_USER)
			attempt=attempt+1

			if attempt>=gparams._ATTEMPTS_BACKEND_READ_INPUT_SOURCES:
				print('(Backend) ERROR: Cannot read input sources!')
				return None

		print('(Backend) DBG: Read input sources - Success')
		return res

	def run_campaign(self):
		try:
			_camp_repet=int(self.db_in_user['Measurement']['Repetitions per campaign'])
			_camp_gap_hours = float(self.db_in_user['Measurement']['Repetition time gap (hours)'])
			_camp_name = self.db_in_user['Measurement']['Campaign name']
			_exp_num=int(self.db_in_user['Measurement']['Experiments per campaign'])

			myline='Initiating campaign with name:'+ str(_camp_name)+',repet='\
			       +str(_camp_repet)+',gap='+str(_camp_gap_hours)+',for exps='+str(_exp_num)
			print('(Backend) DBG: '+str(myline))
			mycsv_line = self.helper.get_str_timestamp()+gparams._DELIMITER+myline
			self.helper.write_db(loc=gparams._DB_FILE_LOC_OUTPUT_LOG, mystr=mycsv_line)
		except Exception as ex:
			print('(Backend) ERROR: At input settings=' + str(ex))
			return None

		self.counter_camp=0
		while (self.counter_camp<_camp_repet):
			# start new campaign repetition
			time_start=self.helper.get_curr_time()

			self.counter_exp=0
			for i in range(0,_exp_num):
				self.run_exp()
				self.counter_exp = self.counter_exp +1

				myline = 'Completed exp:' + str(self.counter_exp) + ',of campaign repetition:' + str(self.counter_camp)
				mycsv_line = self.helper.get_str_timestamp() + gparams._DELIMITER + myline
				self.helper.write_db(loc=gparams._DB_FILE_LOC_OUTPUT_LOG, mystr=mycsv_line)
				print('(Backend) DBG: ' + myline)
				print('---   ---   --- ---   ---   --- ---   ---   --- ')

			self.counter_camp=self.counter_camp+1

			curr_time=self.helper.get_curr_time()
			wait_time_sec=int((3600*_camp_gap_hours)/20)
			while (self.helper.diff_betw_times(time_start,curr_time)<3600*_camp_gap_hours):
				curr_time = self.helper.get_curr_time()
				if (self.helper.diff_betw_times(time_start,curr_time) % 1200==0) or (self.helper.diff_betw_times(time_start,curr_time)<300):
					myline = 'Waiting for new repetition, remaining (sec):' + str(3600*_camp_gap_hours-self.helper.diff_betw_times(time_start,curr_time))
					mycsv_line = self.helper.get_str_timestamp() + gparams._DELIMITER + myline
					self.helper.write_db(loc=gparams._DB_FILE_LOC_OUTPUT_LOG, mystr=mycsv_line)
					print('(Backend) DBG: ' + myline)
				self.helper.wait(wait_time_sec)

	def run_exp(self):
		self.get_baseline_measurements()
		self.get_app_measurements()

	def get_app_measurements(self):
		self.get_app_mqtt()
		self.get_app_video()
		self.get_app_profinet()

	def get_app_mqtt(self):
		try:
			_enable=self.db_in_user['Experiment']['Application']['MQTT']['enable']
			_payload_bytes=int(self.db_in_user['Experiment']['Application']['MQTT']['payload (bytes)'])
			_interval_ms=float(self.db_in_user['Experiment']['Application']['MQTT']['interval (ms)'])
			_shark_captime_sec=float(self.db_in_user['Experiment']['Application']['Wireshark']['capture time (sec)'])
			_shark_max_packs=int(self.db_in_user['Experiment']['Application']['Wireshark']['max packets'])
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Init MQTT test ................')
		except Exception as ex:
			print('(Backend) ERROR: Init MQTT: '+str(ex))
			return None

		if not _enable:
			return None

		config_dict={
			'app_name':'MQTT',
			'client_app_image_name':'client_mqtt',
			'env':{
				'ENV ENV_SERVER_IP' : '127.0.0.1',
				'ENV_SERVER_PORT' : '1234',
				'SLEEP_SEC' : '1',
				'MAX_PAYLOAD_SIZE_BYTES' : '5'
			},
			'shark_captime_sec':_shark_captime_sec,
			'shark_max_packs': _shark_max_packs,
		}

		res=self.activate_app(config_dict=config_dict)

	def activate_app(self,config_dict):
		try:
			_app_name=config_dict['app_name']
			_client_app_image_name=config_dict['client_app_image_name']
			_env=config_dict['env']
			_shark_captime_sec=config_dict['shark_captime_sec']
			_shark_max_packs=config_dict['shark_max_packs']
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Activating app='+str(_app_name)+'...')
		except Exception as ex:
			print('(Backend) ERROR: Activate app:'+str(ex)+'...')
			return None

		# activate app
		orch = Orchestrator()
		iface=orch.activate(image=_client_app_image_name, detach=True, env=_env)

		# monitor stats
		self.get_pyshark_kpis(my_iface=iface,display_filter=None,max_packs=_shark_max_packs,
		                      captime_sec=_shark_captime_sec,camp_name=_camp_name,app_name=_app_name)

		# deactivate app
		orch.deactivate(image=_client_app_image_name)

		print('(Backend) DBG: Get measurements for app=' + str(_app_name) + ' OK!')

		print('(Backend) ERROR: Get measurements for app=' + str(_app_name) + ' failed!')

	def get_pyshark_kpis(self,my_iface='Ethernet',display_filter=None,max_packs=5000,
	                     captime_sec=10,camp_name='',app_name=''):
		print('(Backend) DBG: Initiate pyshark kpis ...')

		# hack to get all available veth-xxx interfaces (not supported by Pyshark)
		#my_iface=None
		#try:
		#	# expect this to fail and raise an exception with all available interfaces
		#	cap=pyshark.LiveCapture(interface='this_is_not_an_interface_100_percent!!', display_filter=display_filter)
		#	cap.sniff(timeout=1)
		#except Exception as ex:
		#	print('(Monitor) DBG: Getting available veth interfaces in the system...')
		#	# get all words of the exception str
		#	word_list=str(ex).split()
		#	for el in word_list:
		#		if 'veth' in el:
		#			my_iface=el
		#			break

		#if my_iface is None:
		#	print('(Monitor) ERROR: No veth ifaces found')
		#	return None


		attempt=1
		res=None
		while (res is None):
			try:
				print('(Backend) DBG: Initiate capture for veth='+str(my_iface)+' (attempt=' + str(attempt) + ')...')
				if attempt > 1:
					self.helper.wait(gparams._WAIT_SEC_BACKEND_READ_INPUT_SOURCES)
				attempt = attempt + 1

				cap = pyshark.LiveCapture(interface=my_iface, display_filter=display_filter,
				                          output_file=gparams._SHARK_TEMP_OUT_FILE)
				cap.sniff_continuously()
				res=200
			except:
				if attempt >= 5:
					print('(Backend) ERROR: Cannot find iface in Pyshark!')
					res = 500
		if res!=200:
			print('(Backend) ERROR: Exiting...')
			return None

		try:
			pack_cnt=0
			start_time = time.time()
			sniff_duration_sec=0

			for pack in cap:
				sniff_duration_sec=time.time() - start_time
				if (pack_cnt>max_packs) or (sniff_duration_sec>captime_sec):
					break
				pack_cnt = pack_cnt + 1
			print('(Backend) DBG: Capture OK, pack_cnt='+str(pack_cnt)+',duration (sec)='+str(sniff_duration_sec))
		except Exception as ex:
			print('(Backend) ERROR during capture:'+str(ex))
			return None

		try:
			cap = pyshark.FileCapture(input_file=gparams._SHARK_TEMP_OUT_FILE)
			pack_cnt=0
			for pack in cap:
				myjson_line=gparams._RES_FILE_FIELDS_APP

				try:
					myjson_line['camp_name'] = camp_name
				except:
					pass

				try:
					myjson_line['repeat_id'] = str(self.counter_camp)
				except:
					pass

				try:
					myjson_line['exp_id'] = str(self.counter_exp)
				except:
					pass

				try:
					myjson_line['timestamp'] = self.helper.get_str_timestamp()
				except:
					pass

				try:
					myjson_line['app_name'] = app_name
				except:
					pass

				try:
					myjson_line['pack_id'] = str(pack_cnt)
				except:
					pass

				try:
					myjson_line['sniff_time'] = str(pack.sniff_time)
				except:
					pass

				try:
					myjson_line['sniff_timestamp'] = str(pack.sniff_timestamp)
				except:
					pass

				try:
					myjson_line['protocol'] = str(pack.highest_layer)
				except:
					pass

				try:
					myjson_line['pack_len_bytes'] = str(pack.length)
				except:
					pass

				try:
					myjson_line['addr_src'] = str(pack.ip.src)
				except:
					pass

				try:
					myjson_line['port_src'] = str(pack[pack.transport_layer].srcport)
				except:
					pass

				try:
					myjson_line['addr_dest'] = str(pack.ip.dst)
				except:
					pass

				try:
					myjson_line['port_dest'] = str(pack[pack.transport_layer].dstport)
				except:
					pass

				try:
					myjson_line['rtt'] = str(pack.tcp.analysis_ack_rtt)
				except:
					pass

				try:
					str_p = str(pack)
					if (
						"TCP Dup ACK" in str_p
						or "TCP Previous" in str_p
						or "TCP Retransmission" in str_p
						or "TCP Fast Retransmission" in str_p
						or "Out-Of-Order" in str_p
						or "TCP Spurious Retransmission" in str_p):
						res = True
					else:
						res = False

					myjson_line['drop_flag'] = str(res)
				except:
					pass

				pack_cnt=pack_cnt+1
				self.helper.write_dict2json(loc=gparams._RES_FILE_LOC_APP, mydict=myjson_line, clean=False)

			try:
				os.remove(gparams._RES_FILE_LOC_APP)
			except:
				pass

			print('(Backend) DBG: Capture analysis OK')
			return 200
		except Exception as ex:
			print('(Backend) ERROR: Capture analysis:'+str(ex))
			return None



	def get_iperf(self):
		try:
			_enable=self.db_in_user['Experiment']['Baseline']['iperf']['enable']
			_protocol = self.db_in_user['Experiment']['Baseline']['iperf']['protocols']
			_payload_bytes = int(self.db_in_user['Experiment']['Baseline']['iperf']['payload (bytes)'])
			_target_rate_mbps=int(self.db_in_user['Experiment']['Baseline']['iperf']['bitrate (Mbps)'])
			_duration_sec=int(self.db_in_user['Experiment']['Baseline']['iperf']['duration (sec)'])
			_server_ip=self.db_in_user['Network']['Server IP']
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Init iperf test ................')
		except Exception as ex:
			print('(Backend) ERROR: Init iperf: '+str(ex))
			return None

		if not _enable:
			return None

		myjson_line = gparams._RES_FILE_FIELDS_IPERF
		myjson_line['camp_name'] = _camp_name
		myjson_line['repeat_id'] = str(self.counter_camp)
		myjson_line['exp_id'] = str(self.counter_exp)
		myjson_line['timestamp'] = self.helper.get_str_timestamp()

		if _protocol in ['TCP','All']:

			# TCP downlink
			data = self.get_iperf_stats(server_ip=_server_ip, port=gparams._PORT_SERVER_IPERF,
			                                      flag_udp=False,flag_downlink=True, duration=_duration_sec,
			                                      bitrate=None,pack_len=_payload_bytes)

			if data is not None:
				try:
					myjson_line['tcp_dl_retransmits'] = data['end']['sum_sent']['retransmits']
					myjson_line['tcp_dl_sent_bps'] = data['end']['sum_sent']['bits_per_second']
					myjson_line['tcp_dl_sent_bytes'] = data['end']['sum_sent']['bytes']
					myjson_line['tcp_dl_received_bps'] = data['end']['sum_received']['bits_per_second']
					myjson_line['tcp_dl_received_bytes'] = data['end']['sum_received']['bytes']
					print('(Backend) DBG: TCP downlink bps ' + str(myjson_line['tcp_dl_received_bps']))
				except Exception as ex:
					print('(Backend) ERROR: TCP downlink write ' + str(ex))

			# TCP uplink
			data = self.get_iperf_stats(server_ip=_server_ip, port=gparams._PORT_SERVER_IPERF,
			                                      flag_udp=False,flag_downlink=False, duration=_duration_sec,
			                                      bitrate=None,pack_len=_payload_bytes)

			if data is not None:
				try:
					myjson_line['tcp_ul_retransmits'] = data['end']['sum_sent']['retransmits']
					myjson_line['tcp_ul_sent_bps'] = data['end']['sum_sent']['bits_per_second']
					myjson_line['tcp_ul_sent_bytes'] = data['end']['sum_sent']['bytes']
					myjson_line['tcp_ul_received_bps'] = data['end']['sum_received']['bits_per_second']
					myjson_line['tcp_ul_received_bytes'] = data['end']['sum_received']['bytes']
					print('(Backend) DBG: TCP uplink bps ' + str(myjson_line['tcp_ul_received_bps']))
				except Exception as ex:
					print('(Backend) ERROR: TCP uplink write ' + str(ex))

		if _protocol in ['UDP','All']:
			_bitrate=str(_target_rate_mbps)+'M'

			# UDP downlink
			data = self.get_iperf_stats(server_ip=_server_ip, port=gparams._PORT_SERVER_IPERF,
			                                      flag_udp=True,flag_downlink=True, duration=_duration_sec,
			                                      bitrate=_bitrate,pack_len=_payload_bytes)

			if data is not None:
				try:
					myjson_line['udp_dl_bytes'] = data['end']['sum']['bytes']
					myjson_line['udp_dl_bps'] = data['end']['sum']['bits_per_second']
					myjson_line['udp_dl_jitter_ms'] = data['end']['sum']['jitter_ms']
					myjson_line['udp_dl_lost_percent'] = data['end']['sum']['lost_percent']
					print('(Backend) DBG: UDP downlink bps ' + str(myjson_line['udp_dl_bps']))
				except Exception as ex:
					print('(Backend) ERROR: UDP downlink write ' + str(ex))

			# UDP uplink
			data = self.get_iperf_stats(server_ip=_server_ip, port=gparams._PORT_SERVER_IPERF,
			                                      flag_udp=True,flag_downlink=False, duration=_duration_sec,
			                                      bitrate=_bitrate,pack_len=_payload_bytes)

			if data is not None:
				try:
					myjson_line['udp_ul_bytes'] = data['end']['sum']['bytes']
					myjson_line['udp_ul_bps'] = data['end']['sum']['bits_per_second']
					myjson_line['udp_ul_jitter_ms'] = data['end']['sum']['jitter_ms']
					myjson_line['udp_ul_lost_percent'] = data['end']['sum']['lost_percent']
					print('(Backend) DBG: UDP uplink bps ' + str(myjson_line['udp_ul_bps']))
				except Exception as ex:
					print('(Backend) ERROR: UDP uplink write ' + str(ex))

		self.helper.write_dict2json(loc=gparams._RES_FILE_LOC_IPERF, mydict=myjson_line, clean=False)

	def get_iperf_stats(self,server_ip,port=5201,flag_udp=False,flag_downlink=False,duration=10,bitrate=None,
	                    pack_len=None):
		print('(Backend) DBG: Entered iperf3 stats at:'+str(self.helper.get_str_timestamp()))
		print('(Backend) DBG: Settings: UDP='+str(flag_udp)+
		      ',Downlink='+str(flag_downlink)+
		      ',bitrate='+str(bitrate)+
		      ',duration='+str(duration)+
			  ',pack_len='+str(pack_len)+
		      '...')
		# init iperf3
		cmd=['iperf3']

		# add server IP
		cmd.append('--client')
		cmd.append(str(server_ip))

		# add server port
		cmd.append('--port')
		cmd.append(str(port))

		# duration in sec
		cmd.append('--time')
		cmd.append(str(duration))

		# bitrate in bps
		if bitrate is not None:
			cmd.append('--bitrate')
			cmd.append(str(bitrate))

		# check if reverse (uplink if the default in iperf, from client to server)
		if flag_downlink:
			cmd.append('--reverse')

		# check if udp, default is tcp
		if flag_udp:
			cmd.append('--udp')

		if pack_len is not None:
			cmd.append('--length')
			cmd.append(str(pack_len))

		cmd.append('--json')

		result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		output= result.stdout

		try:
			data = json.loads(output)
			return data
		except Exception as ex:
			print('(Monitor) ERROR in iperf3 output json='+str(ex))
			return None


	def get_icmp(self):
		try:
			_enable=self.db_in_user['Experiment']['Baseline']['icmp']['enable']
			_payload_bytes = int(self.db_in_user['Experiment']['Baseline']['icmp']['payload (bytes)'])
			_interval_ms=int(self.db_in_user['Experiment']['Baseline']['icmp']['interval (ms)'])
			_packets=int(self.db_in_user['Experiment']['Baseline']['icmp']['packets'])
			_server_ip=self.db_in_user['Network']['Server IP']
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Init ICMP ping test ................')
		except Exception as ex:
			print('(Backend) ERROR: Init ICMP ping: '+str(ex))
			return None

		if not _enable:
			return None

		myjson_line = gparams._RES_FILE_FIELDS_ICMP
		myjson_line['camp_name'] = _camp_name
		myjson_line['repeat_id'] = str(self.counter_camp)
		myjson_line['exp_id'] = str(self.counter_exp)
		myjson_line['timestamp'] = self.helper.get_str_timestamp()

		_interval_sec=_interval_ms*1e-3
		data = self.get_icmp_stats(server_ip=_server_ip,packs=_packets,
		                           interval_sec=_interval_sec,payload_bytes=_payload_bytes)

		if data is not None:
			try:
				myjson_line['min_rtt_ms'] = data.min_rtt
				myjson_line['avg_rtt_ms'] = data.avg_rtt
				myjson_line['max_rtt_ms'] = data.max_rtt
				myjson_line['rtts_ms']=data.rtts
				myjson_line['packets_sent'] = data.packets_sent
				myjson_line['packets_received'] = data.packets_received
				myjson_line['packet_loss_0to1'] = data.packet_loss
				myjson_line['jitter_ms'] = data.jitter
			except Exception as ex:
				print('(Backend) ERROR: ICMP ping write=' + str(ex))

		self.helper.write_dict2json(loc=gparams._RES_FILE_LOC_ICMP, mydict=myjson_line, clean=False)

	def get_icmp_stats(self,server_ip,packs=50,interval_sec=1,payload_bytes=64):
		print('(Monitor) DBG: Entered ICMP ping stats at:' + str(self.helper.get_str_timestamp()))
		print('(Monitor) DBG: Settings: server_ip=' + str(server_ip) + ',num_packets=' + str(packs) +
			  ',interval_sec=' + str(interval_sec) + ',payload_bytes='+str(payload_bytes)+' ...')

		try:
			# ping has a max packet len around 1500 bytes
			res = ping(server_ip, count=packs, interval=interval_sec,payload_size=payload_bytes,privileged=False,timeout=0.5)
			print('(Monitor) DBG: Ping res=' + str(res))

			if res.is_alive:
				print('(Monitor) DBG: Ping alive!')
				return res
			else:
				print('(Monitor) DBG: Ping NOT alive!')
				return None

		except Exception as ex:
			print('(Monitor) ERROR: Ping failed=' + str(ex))
			return None

	def get_udpping(self):
		try:
			_enable=self.db_in_user['Experiment']['Baseline']['icmp']['enable']
			_payload_bytes = int(self.db_in_user['Experiment']['Baseline']['icmp']['payload (bytes)'])
			_interval_ms=int(self.db_in_user['Experiment']['Baseline']['icmp']['interval (ms)'])
			_packets=int(self.db_in_user['Experiment']['Baseline']['icmp']['packets'])
			_server_ip=self.db_in_user['Network']['Server IP']
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Init UDP ping test ................')
		except Exception as ex:
			print('(Backend) ERROR: Init UDP ping: '+str(ex))
			return None

		if not _enable:
			return None

		myjson_line = gparams._RES_FILE_FIELDS_UDPPING
		data_df = self.get_udpping_stats(server_ip=_server_ip,payload_bytes=_payload_bytes,
		                                 packs=_packets,interval_ms=_interval_ms)

		if data_df is not None:
			try:
				data_df['camp_name'] = _camp_name
				data_df['repeat_id'] = str(self.counter_camp)
				data_df['exp_id'] = str(self.counter_exp)
				data_df['timestamp'] = self.helper.get_str_timestamp()

				data_df.to_json(gparams._RES_FILE_LOC_UDPPING,orient='records', lines=True)
			except Exception as ex:
				print('(Backend) ERROR: UDP ping write=' + str(ex))

	def get_udpping_stats(self,server_ip,payload_bytes=1250,packs=5000,interval_ms=20,port=1234):
		print('(Monitor) DBG: Entered udpPing at:'+str(self.helper.get_str_timestamp()))
		print('(Monitor) DBG: Settings: payload_bytes='+str(payload_bytes)+',packs='+str(packs)+
			  ',interval_ms='+str(interval_ms)+'...')
		# get loc
		try:
			mypath=gparams._UDPPING_ROOT
			cmd=[]
			#cmd.append(str(mypath))
			#cmd.append('&&')
			cmd.append('./udpClient')

			# add server IP
			cmd.append('-a')
			cmd.append(str(server_ip))

			# add packet size
			cmd.append('-s')
			cmd.append(str(payload_bytes))

			# num_packets
			cmd.append('-n')
			cmd.append(str(packs))

			# interval_ms
			cmd.append('-i')
			cmd.append(str(interval_ms))

			out = subprocess.check_output(cmd,cwd=mypath)

			my_strs = (str(out)).split('(all times in ns)')
			temp_str = my_strs[1].split('out of')
			final_str = temp_str[0]
			final_str = final_str.replace('\\n', '$')
			final_str = final_str.replace('\n', '$')
			final_str = final_str.replace('$', '\n')
			df_str = StringIO(final_str)

			df = pd.read_table(df_str, sep=gparams._UDPPING_DELIMITER, header=None)
			df.columns = gparams._RES_FILE_FIELDS_UDPPING

			temp_df=df.head(1)
			print('(Backend) Result=' + str(temp_df))

			return df
		except Exception as ex:
			print('(Backend) ERROR cannot process udpPing='+str(ex))
			return None

	def get_owamp(self):
		try:
			_enable=self.db_in_user['Experiment']['Baseline']['wamp']['enable']
			_payload_bytes = int(self.db_in_user['Experiment']['Baseline']['wamp']['payload (bytes)'])
			_interval_ms=int(self.db_in_user['Experiment']['Baseline']['wamp']['interval (ms)'])
			_packets=int(self.db_in_user['Experiment']['Baseline']['wamp']['packets'])
			_server_ip=self.db_in_user['Network']['Server IP']
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Init OWAMP test ................')
		except Exception as ex:
			print('(Backend) ERROR: Init OWAMP: '+str(ex))
			return None

		if not _enable:
			return None

		myjson_line = gparams._RES_FILE_FIELDS_OWAMP
		data_df = self.get_owamp_stats(server_ip=_server_ip,payload_bytes=_payload_bytes,
		                                 packs=_packets,interval_ms=_interval_ms)

		if data_df is not None:
			try:
				data_df['camp_name'] = _camp_name
				data_df['repeat_id'] = str(self.counter_camp)
				data_df['exp_id'] = str(self.counter_exp)
				data_df['timestamp'] = self.helper.get_str_timestamp()

				data_df.to_json(gparams._RES_FILE_LOC_OWAMP,orient='records', lines=True)
			except Exception as ex:
				print('(Backend) ERROR: OWAMP write=' + str(ex))

	def get_owamp_stats(self,server_ip,payload_bytes=1250,packs=5000,interval_ms=20):
		print('(Monitor) DBG: Entered OWAMP at:'+str(self.helper.get_str_timestamp()))
		print('(Monitor) DBG: Settings: payload_bytes='+str(payload_bytes)+',packs='+str(packs)+
			  ',interval_ms='+str(interval_ms)+'...')
		# get loc
		try:
			cmd = ['owping']

			cmd.append('-c')
			cmd.append(str(packs))

			cmd.append('-s')
			cmd.append(str(payload_bytes))

			interval_sec=interval_ms*1e-3
			cmd.append('-i')
			cmd.append(str(interval_sec))

			cmd.append('-R')

			cmd.append(str(server_ip))

			result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
			output = result.stdout

			output = output.replace(' ', gparams._OWAMP_DELIMITER)
			output = output.replace('\n', '$')
			output = output.replace('$', '\n')
			df_str = StringIO(output)

			df = pd.read_table(df_str, sep=gparams._OWAMP_DELIMITER, header=None)
			df.columns = gparams._RES_FILE_FIELDS_OWAMP

			df['is_previous_larger'] = (df[gparams._KEY_WORD_OWAMP].shift(1) > df[gparams._KEY_WORD_OWAMP]).astype(int)
			mylist = df.index[df['is_previous_larger'] == 1].tolist()
			df = df.drop('is_previous_larger', axis=1)
			sep_raw = mylist[0]

			df.loc[:sep_raw, 'direction'] = 'ul'
			df.loc[sep_raw:, 'direction'] = 'dl'
			print('(Backend) DBG OWAMP tx sync status='+str(df[gparams._DBG_KEY_WORD_OWAMP].mean()))
			return df
		except Exception as ex:
			print('(Backend) ERROR cannot process OWAMP='+str(ex))
			return None

	def get_twamp(self):
		try:
			_enable=self.db_in_user['Experiment']['Baseline']['wamp']['enable']
			_payload_bytes = int(self.db_in_user['Experiment']['Baseline']['wamp']['payload (bytes)'])
			_interval_ms=int(self.db_in_user['Experiment']['Baseline']['wamp']['interval (ms)'])
			_packets=int(self.db_in_user['Experiment']['Baseline']['wamp']['packets'])
			_server_ip=self.db_in_user['Network']['Server IP']
			_camp_name=self.db_in_user['Measurement']['Campaign name']
			print('(Backend) DBG: Init TWAMP test ................')
		except Exception as ex:
			print('(Backend) ERROR: Init TWAMP: '+str(ex))
			return None

		if not _enable:
			return None

		myjson_line = gparams._RES_FILE_FIELDS_TWAMP
		data_df = self.get_twamp_stats(server_ip=_server_ip,payload_bytes=_payload_bytes,
		                                 packs=_packets,interval_ms=_interval_ms)

		if data_df is not None:
			try:
				data_df['camp_name'] = _camp_name
				data_df['repeat_id'] = str(self.counter_camp)
				data_df['exp_id'] = str(self.counter_exp)
				data_df['timestamp'] = self.helper.get_str_timestamp()

				data_df.to_json(gparams._RES_FILE_LOC_TWAMP,orient='records', lines=True)
			except Exception as ex:
				print('(Backend) ERROR: OWAMP write=' + str(ex))

	def get_twamp_stats(self,server_ip,payload_bytes=1250,packs=5000,interval_ms=20):
		print('(Monitor) DBG: Entered TWAMP at:'+str(self.helper.get_str_timestamp()))
		print('(Monitor) DBG: Settings: payload_bytes='+str(payload_bytes)+',packs='+str(packs)+
			  ',interval_ms='+str(interval_ms)+'...')
		# get loc
		try:
			cmd = ['twping']

			cmd.append('-c')
			cmd.append(str(packs))

			cmd.append('-s')
			cmd.append(str(payload_bytes))

			interval_sec=interval_ms*1e-3
			cmd.append('-i')
			cmd.append(str(interval_sec))

			cmd.append('-R')

			cmd.append(str(server_ip))

			result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
			output = result.stdout

			output = output.replace(' ', gparams._TWAMP_DELIMITER)
			output = output.replace('\n', '$')
			output = output.replace('$', '\n')
			df_str = StringIO(output)

			df = pd.read_table(df_str, sep=gparams._TWAMP_DELIMITER, header=None)
			df.columns = gparams._RES_FILE_FIELDS_TWAMP
			print('(Backend) DBG OWAMP tx sync status='+str(df[gparams._DBG_KEY_WORD_OWAMP].mean()))
			return df
		except Exception as ex:
			print('(Monitor) ERROR cannot process TWAMP='+str(ex))
			return None

	def get_baseline_measurements(self):
		self.get_iperf()
		self.get_icmp()
		self.get_udpping()
		self.get_owamp()
		self.get_twamp()


if __name__ == '__main__':
	print('(Backend) DBG: Backend initialized')
	pd.options.mode.chained_assignment = None  # default='warn'
	backend=Backend()