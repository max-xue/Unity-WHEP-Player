using Unity.WebRTC;
using UnityEngine;
using UnityEngine.Audio;

public class RemoteAudioProcessor : MonoBehaviour
{
	public AudioMixerGroup remoteAudioMixerGroup;

	// 在 PeerConnection 添加远端音频流时调用
	public void AttachToRemoteTrack(AudioStreamTrack remoteTrack)
	{
		var source = gameObject.AddComponent<AudioSource>();
		source.loop = true;
		source.playOnAwake = true;
		source.outputAudioMixerGroup = remoteAudioMixerGroup;

		// 把 WebRTC 音频绑定到 AudioSource
		source.SetTrack(remoteTrack);
		source.Play();
	}
}
