using System;
using System.Collections;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;
using UnityWebSocket;

public class AudioManager : MonoBehaviour
{
	public string IP = "218.78.129.228";
	public string RobotName = "";

	private int _lastPoint;
	private float _delayRecordTime = 2.5f;
	private bool _isRecording;
	private bool _isStopRecord;
	private bool _readyToGetFrame;
	private bool _isOpenAudio;
	private string _micDevicesName;

	private Queue<byte[]> _audioDataQueue;
	private AudioClip _recordClip;
	private AudioSource _speakerAudio;

	private void Awake()
	{

	}

	private void Start()
	{
		InitData();
		SetMicDevicesName();

		CreateWebSocket(IP, 8008, RobotName);
		StartRecord();
	}

	private void Reset()
	{
		InitData();
	}

	private void WebSocketOpenMethods(object sender, OpenEventArgs e)
	{
	}

	private void WebSocketCloseMethods(object sender, CloseEventArgs e)
	{
		if (!_isOpenAudio) return;

		StopRecording();
		CloseWebSocket();
	}

	private void InitData()
	{
		_lastPoint = 0;
		_isRecording = false;
		_isStopRecord = false;
		_readyToGetFrame = false;
		_isOpenAudio = true;
		_audioDataQueue = new Queue<byte[]>();
		_speakerAudio = GetComponent<AudioSource>();
	}

	private void PlayAudio(AudioClip clip)
	{
		if (_speakerAudio == null) return;
		_speakerAudio.clip = clip;
		_speakerAudio.Play();
	}

	public void StopAudio()
	{
		if (_speakerAudio == null) return;
		_speakerAudio.Stop();
	}

	//public void PlayAudio(string key)
	//{
	//	if (string.IsNullOrEmpty(key)) return;
	//	LocalizationManager.Instance.GetLocalAudioAsync(key, (clip) => {
	//		PlayAudio(clip);
	//	});
	//}

	#region 发送麦克风音频数据

	private void SetMicDevicesName()
	{
#if UNITY_EDITOR
		if (Microphone.devices.Length <= 3)
		{
			_micDevicesName = Microphone.devices[0];
		}
		else
		{
			_micDevicesName = Microphone.devices[3];
		}
#else
		_micDevicesName = Microphone.devices[0];
#endif
	}

	public static int FREQUENCY = 48000;
	private IEnumerator StartSendBytes()
	{
		_recordClip = Microphone.Start(_micDevicesName, true, 60, FREQUENCY);
		yield return new WaitForSecondsRealtime(_delayRecordTime);
		_lastPoint = Microphone.GetPosition(_micDevicesName);
		while (!_isStopRecord)
		{
			_readyToGetFrame = false;
			SendBytes();
			while (!_readyToGetFrame)
			{
				if (_isStopRecord)
				{
					break;
				}
				yield return Task.Delay(5);
			}
		}
	}

	/// <summary>
	/// 音频转bytes
	/// </summary>
	/// <param name="clip"></param>
	/// <returns></returns>
	public static byte[] FloatToByte(float[] data)
	{
		int rescaleFactor = 32767; //to convert float to Int16
		byte[] outData = new byte[data.Length * 2];
		for (int i = 0; i < data.Length; i++)
		{
			short temshort = (short)(data[i] * rescaleFactor);
			outData[i * 2] = (byte)(temshort & 0xff);
			outData[i * 2 + 1] = (byte)((temshort >> 8) & 0xff);
		}
		return outData;
	}


	private async void SendBytes()
	{
		await Task.Delay(500);
		int point = Microphone.GetPosition(_micDevicesName);

		if (point > _lastPoint)
		{
			float[] audioFloats = new float[point - _lastPoint];
			if (_recordClip == null) return;
			_recordClip.GetData(audioFloats, _lastPoint);
			_lastPoint = point;
			byte[] audioBytes = FloatToByte(audioFloats);
			SendSubData(audioBytes);
		}
		else
		{
			_lastPoint = 0;
		}
		_readyToGetFrame = true;
	}

	private void SendSubData(byte[] data)
	{
		try
		{
			int dataSize = data.Length;
			int bytesSent = 0;
			int maxChunkSize = 1024;

			while (bytesSent < dataSize)
			{
				int packetSize = Math.Min(maxChunkSize, dataSize - bytesSent);
				byte[] packet = new byte[packetSize];
				Array.Copy(data, bytesSent, packet, 0, packetSize);
				SendMsg(packet);
				bytesSent += packetSize;
			}
		}
		catch (Exception e)
		{
			Debug.LogError($"Error sending data: {e.Message}");
		}
	}

	private void StartRecord()
	{
		if (_isRecording) return;
		_isRecording = true;
		StartCoroutine(StartSendBytes());
	}

	private void StopRecording()
	{
		if (!_isRecording) return;
		_isRecording = false;
		Microphone.End(null);
		_isStopRecord = true;
	}
	#endregion

	private void OnDestroy()
	{
		if (!_isOpenAudio) return;
		StopRecording();
		CloseWebSocket();
	}

	#region Websocket

	private WebSocket _webSocket;
	public void CreateWebSocket(string ip, int port, string robotName)
	{
		string address = $"ws://{ip}:{port}/terminal_audio/{robotName}";
		_webSocket = new WebSocket(address);

		_webSocket.OnOpen += Socket_OnOpen;
		_webSocket.OnClose += Socket_OnClose;
		_webSocket.OnError += Socket_OnError;
		_webSocket.OnMessage += Socket_OnMessage;
		_webSocket.ConnectAsync();
	}

	public void CloseWebSocket()
	{
		if (_webSocket != null && _webSocket.ReadyState != WebSocketState.Closed)
		{
			_webSocket.CloseAsync();
		}
	}

	public void SendMsg(string msg)
	{
		if (_webSocket != null && _webSocket.ReadyState == WebSocketState.Open)
		{
			_webSocket.SendAsync(msg);
		}
	}

	public void SendMsg(byte[] msg)
	{
		if (_webSocket != null && _webSocket.ReadyState == WebSocketState.Open)
		{
			_webSocket.SendAsync(msg);
		}
	}

	private void Socket_OnError(object sender, ErrorEventArgs e)
	{
	}

	private void Socket_OnMessage(object sender, MessageEventArgs args)
	{
	}

	private void Socket_OnClose(object sender, CloseEventArgs e)
	{
	}

	private void Socket_OnOpen(object sender, OpenEventArgs e)
	{
		Debug.Log("Socket_OnOpen");
	}

	#endregion
}

