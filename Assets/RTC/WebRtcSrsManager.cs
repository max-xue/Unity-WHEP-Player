using System.Collections;
using Unity.WebRTC;
using UnityEngine;
using UnityEngine.UI;

public class WebRtcSrsManager : MonoBehaviour
{
	public AudioSource ReceiveAudio;
	public RawImage RawImage;
	public string URL;

	private string url = "http://localhost:1985/rtc/v1/whep/?app=live&stream=livestream";
	//private string _curUrl;
	private RawImage _receiveImage;
	private MediaStream receiveStream;
	private RTCPeerConnection pc;

	private static WebRtcSrsManager _instance;
	public static WebRtcSrsManager Instance
	{
		get
		{
			if (_instance == null)
			{
				Debug.LogWarning("WebRtcSrsManager instance is null");
			}
			return _instance;
		}
	}

	private void Awake()
	{
		_instance = this;
		InitWebrtc();
	}

	private void Start()
	{
		StartCoroutine(CoStartWebrtc(""));
	}

	private void OnDestroy()
	{
		StopWebrtc();
	}

	private void SetWebrtcIp(string ip)
	{
		//string ip = HttpRequestManager.Instance.IP;

		//var robotName = RobotCtrlManager.Instance.GetController<MenuCtrl>().CurrentConnectItem.Name;
		//_curUrl = url.Replace("localhost", ip).Replace("livestream", robotName);

		//_curUrl = "http://192.168.10.9:1985/rtc/v1/whep/?app=live&stream=livestream";
		//_curUrl = "http://192.168.10.9:1985/rtc/v1/whep/?app=live&stream=stream&codec=hevc";
	}

	private IEnumerator CoStartWebrtc(string ip)
	{
		yield return new WaitForSeconds(0.1f);

		SetWebrtcIp(ip);
		StartWebrtc();
	}

	private void InitWebrtc()
	{
#if WEBRTC_3_0_0_PRE_5_OR_BEFORE
		WebRTC.Initialize();
#endif
		Debug.Log("WebRTC: Initialize ok");
	}

	public void StartWebrtc()
	{
		Debug.Log($"WebRTC: Start to play {URL}");

		// Start WebRTC update.
		StartCoroutine(WebRTC.Update());

		// Create object only after WebRTC initialized.
		pc = new RTCPeerConnection();
		receiveStream = new MediaStream();

		RTCRtpCodecCapability[] codecs = null;
		var capabilities = RTCRtpSender.GetCapabilities(TrackKind.Video);
		var availableCodecs = capabilities.codecs;
		for (int i = 0; i < availableCodecs.Length; ++i)
		{
			var item = availableCodecs[i];
			Debug.Log($"RTCRtpSender all codec index {i} codec.channels = {item.channels} " +
				$"codec.clockRate = {item.clockRate} codec.mimeType codec.sdpFmtpLine = {item.mimeType + " " + item.sdpFmtpLine}");
		}

		capabilities = RTCRtpReceiver.GetCapabilities(TrackKind.Video);
		availableCodecs = capabilities.codecs;
		for (int i = 0; i < availableCodecs.Length; ++i)
		{
			var item = availableCodecs[i];
			Debug.Log($"RTCRtpReceiver all codec index {i} codec.channels = {item.channels} " +
				$"codec.clockRate = {item.clockRate} codec.mimeType codec.sdpFmtpLine = {item.mimeType + " " + item.sdpFmtpLine}");
		}

		// Setup player peer connection.
		pc.OnIceCandidate = candidate =>
		{
			Debug.Log($"WebRTC: OnIceCandidate {candidate.ToString()}");
		};
		pc.OnIceConnectionChange = state =>
		{
			Debug.Log($"WebRTC: OnIceConnectionChange {state.ToString()}");
		};
		pc.OnTrack = e =>
		{
			receiveStream.AddTrack(e.Track);
		};

		// Setup player media stream.
		receiveStream.OnAddTrack = e =>
		{
			Debug.Log($"WebRTC: OnAddTrack {e.ToString()}");
			if (e.Track is VideoStreamTrack videoTrack)
			{
				videoTrack.OnVideoReceived += tex =>
				{
					Debug.Log($"WebRTC: OnVideoReceived {videoTrack.ToString()}, tex={tex.width}x{tex.height}");
					//var cameraCtrl = RobotCtrlManager.Instance.GetController<CameraCtrl>();
					//cameraCtrl.SetWebRtcImage(tex);
					//cameraCtrl.SetSceneMode();
					//if (_receiveImage == null) return;
					//_receiveImage.texture = tex;
					//var width = tex.width < 1280 ? tex.width : 1280;
					//var height = tex.width > 0 ? width * tex.height / tex.width : 720;
					//_receiveImage.rectTransform.sizeDelta = new Vector2(width, height);
					RawImage.texture = tex;
				};
			}
			else if (e.Track is AudioStreamTrack audioTrack)
			{
				Debug.Log($"WebRTC: OnAudioReceived {audioTrack.ToString()}");
				if (ReceiveAudio == null) return;
				ReceiveAudio.SetTrack(audioTrack);
				ReceiveAudio.loop = true;
				ReceiveAudio.Play();
			}
		};

		// Setup PeerConnection to receive stream only.
		StartCoroutine(SetupPeerConnection());
		IEnumerator SetupPeerConnection()
		{
			RTCRtpTransceiverInit init = new RTCRtpTransceiverInit();
			init.direction = RTCRtpTransceiverDirection.SendRecv;
			pc.AddTransceiver(TrackKind.Audio, init);
			pc.AddTransceiver(TrackKind.Video, init);

			yield return StartCoroutine(PeerNegotiationNeeded());
		}

		// Generate offer.
		IEnumerator PeerNegotiationNeeded()
		{
			var op = pc.CreateOffer();
			yield return op;

			Debug.Log($"WebRTC: CreateOffer done={op.IsDone}, hasError={op.IsError}, {op.Desc}");
			if (op.IsError) yield break;

			yield return StartCoroutine(OnCreateOfferSuccess(op.Desc));
		}

		// When offer is ready, set to local description.
		IEnumerator OnCreateOfferSuccess(RTCSessionDescription offer)
		{
			var op = pc.SetLocalDescription(ref offer);
			Debug.Log($"WebRTC: SetLocalDescription {offer.type} {offer.sdp}");
			yield return op;

			Debug.Log($"WebRTC: Offer done={op.IsDone}, hasError={op.IsError}");
			if (op.IsError) yield break;

			yield return StartCoroutine(ExchangeSDP(URL, offer.sdp));
		}

		// Exchange SDP(offer) with server, got answer.
		IEnumerator ExchangeSDP(string url, string offer)
		{
			// Use Task to call async methods.
			var task = System.Threading.Tasks.Task<string>.Run(async () =>
			{
				System.Uri uri = new System.UriBuilder(url).Uri;
				Debug.Log($"WebRTC: Build uri {uri}");

				var content = new System.Net.Http.StringContent(offer);
				content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/sdp");

				var client = new System.Net.Http.HttpClient();
				var res = await client.PostAsync(uri, content);
				res.EnsureSuccessStatusCode();

				string data = await res.Content.ReadAsStringAsync();
				Debug.Log($"WebRTC: Exchange SDP ok, answer is {data}");
				return data;
			});

			// Covert async to coroutine yield, wait for task to be completed.
			yield return new WaitUntil(() => task.IsCompleted);
			// Check async task exception, it won't throw it automatically.
			if (task.Exception != null)
			{
				Debug.Log($"WebRTC: Exchange SDP failed, url={url}, err is {task.Exception.ToString()}");
				yield break;
			}

			StartCoroutine(OnGotAnswerSuccess(task.Result));
		}

		// When got answer, set remote description.
		IEnumerator OnGotAnswerSuccess(string answer)
		{
			RTCSessionDescription desc = new RTCSessionDescription();
			desc.type = RTCSdpType.Answer;
			desc.sdp = answer;
			var op = pc.SetRemoteDescription(ref desc);
			yield return op;

			Debug.Log($"WebRTC: Answer done={op.IsDone}, hasError={op.IsError}");
			yield break;
		}
	}

	public void StopWebrtc()
	{
		if (pc != null)
		{
			pc.Close();
			pc.Dispose();
			pc = null;
		}
#if WEBRTC_3_0_0_PRE_5_OR_BEFORE
        WebRTC.Dispose();
#endif
		Debug.Log("WebRTC: Dispose ok");
	}
}