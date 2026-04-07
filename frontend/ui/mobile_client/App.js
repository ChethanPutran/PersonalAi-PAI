import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  TextInput,
  Alert,
  Platform,
  PermissionsAndroid,
  AppState,
  Dimensions,
  Modal,
  ActivityIndicator,
  Vibration,
  KeyboardAvoidingView,
  SafeAreaView,
  RefreshControl,
} from 'react-native';
import Voice from '@react-native-voice/voice';
import RNFS from 'react-native-fs';
import DocumentPicker from 'react-native-document-picker';
import PushNotification from 'react-native-push-notification';
import PushNotificationIOS from '@react-native-community/push-notification-ios';
import AudioRecord from 'react-native-audio-record';
import { Buffer } from 'buffer';
import moment from 'moment';

const { width, height } = Dimensions.get('window');

// Configuration - Update this with your server IP
const BACKEND_HOST = '192.168.1.100'; // Change to your computer's IP
const BACKEND_PORT = '8000';
const WS_URL = `ws://${BACKEND_HOST}:${BACKEND_PORT}`;
const API_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

const App = () => {
  // State Management
  const [isListening, setIsListening] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [messages, setMessages] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [vadConfidence, setVadConfidence] = useState(0);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [currentTaskId, setCurrentTaskId] = useState(null);
  const [isApprovalModalVisible, setIsApprovalModalVisible] = useState(false);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [showFileMenu, setShowFileMenu] = useState(false);
  const [userFiles, setUserFiles] = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [inputText, setInputText] = useState('');
  const [isDarkMode, setIsDarkMode] = useState(false);
  
  // Refs
  const wsRef = useRef(null);
  const audioStreamRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);
  const messagesEndRef = useRef(null);
  
  // Wake word configuration
  const WAKE_WORD = 'hey bot';
  const lastWakeWordTime = useRef(0);
  const wakeWordCooldown = 2000; // 2 seconds
  
  useEffect(() => {
    initApp();
    
    return () => {
      cleanup();
    };
  }, []);
  
  useEffect(() => {
    scrollToBottom();
  }, [messages]);
  
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollToEnd({ animated: true });
  };
  
  const initApp = async () => {
    await requestPermissions();
    await initSession();
    await initPushNotifications();
    initAudioRecording();
    initVoiceListener();
    
    // App state listener for background/foreground
    AppState.addEventListener('change', handleAppStateChange);
  };
  
  const handleAppStateChange = (nextAppState) => {
    if (nextAppState === 'active') {
      // App came to foreground
      checkConnectionAndReconnect();
    } else if (nextAppState === 'background') {
      // App went to background - stop VAD to save battery
      if (isListening) {
        stopVoiceDetection();
      }
    }
  };
  
  const checkConnectionAndReconnect = () => {
    if (!isConnected && sessionId) {
      console.log('Reconnecting to WebSocket...');
      connectWebSocket(sessionId);
    }
  };
  
  const requestPermissions = async () => {
    if (Platform.OS === 'android') {
      try {
        const permissions = await PermissionsAndroid.requestMultiple([
          PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
          PermissionsAndroid.PERMISSIONS.READ_EXTERNAL_STORAGE,
          PermissionsAndroid.PERMISSIONS.WRITE_EXTERNAL_STORAGE,
          PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
        ]);
        
        const allGranted = Object.values(permissions).every(
          permission => permission === PermissionsAndroid.RESULTS.GRANTED
        );
        
        if (!allGranted) {
          Alert.alert('Permissions Required', 'Please grant all permissions to use the app');
        }
      } catch (err) {
        console.warn(err);
      }
    } else if (Platform.OS === 'ios') {
      // iOS permissions are requested automatically
      PushNotificationIOS.requestPermissions();
    }
  };
  
  const initSession = async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/session/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 'mobile_user' })
      });
      
      const data = await response.json();
      setSessionId(data.session_id);
      
      // Connect WebSocket
      connectWebSocket(data.session_id);
      
      addMessage('system', 'Session created successfully');
    } catch (error) {
      console.error('Session init error:', error);
      addMessage('error', 'Failed to connect to server. Check your connection.');
    }
  };
  
  const connectWebSocket = (sessionId) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.close();
    }
    
    const ws = new WebSocket(`${WS_URL}/ws/${sessionId}`);
    
    ws.onopen = () => {
      console.log('WebSocket connected');
      setIsConnected(true);
      addMessage('system', 'Connected to assistant');
      
      // Start heartbeat
      startHeartbeat();
    };
    
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        handleServerMessage(message);
      } catch (error) {
        console.error('Message parse error:', error);
      }
    };
    
    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      setIsConnected(false);
      addMessage('error', 'Connection error');
    };
    
    ws.onclose = () => {
      console.log('WebSocket disconnected');
      setIsConnected(false);
      
      // Stop heartbeat
      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current);
      }
      
      // Reconnect after 5 seconds
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      reconnectTimeoutRef.current = setTimeout(() => {
        if (sessionId) {
          connectWebSocket(sessionId);
        }
      }, 5000);
    };
    
    wsRef.current = ws;
  };
  
  const startHeartbeat = () => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
    }
    
    heartbeatIntervalRef.current = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'ping',
          content: 'ping',
          session_id: sessionId,
          timestamp: new Date().toISOString()
        }));
      }
    }, 30000); // Every 30 seconds
  };
  
  const initPushNotifications = () => {
    PushNotification.configure({
      onNotification: (notification) => {
        console.log('Push notification:', notification);
        handlePushNotification(notification);
        
        // Required for iOS
        if (Platform.OS === 'ios') {
          notification.finish(PushNotificationIOS.FetchResult.NoData);
        }
      },
      onRegister: (token) => {
        console.log('FCM Token:', token);
        registerDeviceToken(token.token);
      },
      popInitialNotification: true,
      requestPermissions: Platform.OS === 'ios',
    });
    
    // Create notification channel for Android
    if (Platform.OS === 'android') {
      PushNotification.createChannel({
        channelId: 'openclaw',
        channelName: 'OpenClaw Assistant',
        channelDescription: 'Notifications from OpenClaw Assistant',
        soundName: 'default',
        importance: 4,
        vibrate: true,
        playSound: true,
      });
    }
  };
  
  const registerDeviceToken = async (fcmToken) => {
    if (!sessionId) return;
    
    try {
      await fetch(`${API_URL}/api/v1/notifications/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          session_id: sessionId, 
          fcm_token: fcmToken 
        })
      });
      console.log('Device token registered');
    } catch (error) {
      console.error('Token registration error:', error);
    }
  };
  
  const initAudioRecording = () => {
    AudioRecord.init({
      sampleRate: 16000,
      channels: 1,
      bitsPerSample: 16,
      audioSource: 6, // Voice recognition
      wavFile: 'recording.wav'
    });
  };
  
  const initVoiceListener = () => {
    Voice.onSpeechStart = onSpeechStart;
    Voice.onSpeechEnd = onSpeechEnd;
    Voice.onSpeechResults = onSpeechResults;
    Voice.onSpeechError = onSpeechError;
    Voice.onSpeechPartialResults = onSpeechPartialResults;
    Voice.onSpeechVolumeChanged = onSpeechVolumeChanged;
  };
  
  const onSpeechStart = () => {
    console.log('Speech started');
    setIsProcessing(true);
  };
  
  const onSpeechEnd = () => {
    console.log('Speech ended');
    setIsProcessing(false);
  };
  
  const onSpeechResults = (e) => {
    const text = e.value[0];
    console.log('Recognized:', text);
    processVoiceCommand(text);
  };
  
  const onSpeechPartialResults = (e) => {
    const text = e.value[0];
    console.log('Partial:', text);
    // Optional: Show real-time transcription
  };
  
  const onSpeechVolumeChanged = (e) => {
    const volume = e.value;
    setVadConfidence(volume / 100);
  };
  
  const onSpeechError = (e) => {
    console.error('Voice error:', e);
    setIsListening(false);
    setIsProcessing(false);
    Alert.alert('Voice Error', 'Could not recognize speech. Please try again.');
  };
  
  const startVoiceDetection = async () => {
    try {
      await Voice.start('en-US');
      setIsListening(true);
      Vibration.vibrate(100);
      addMessage('system', '🎤 Listening... Say something');
    } catch (error) {
      console.error('Voice start error:', error);
      Alert.alert('Error', 'Could not start voice recognition');
    }
  };
  
  const stopVoiceDetection = async () => {
    try {
      await Voice.stop();
      setIsListening(false);
    } catch (error) {
      console.error('Voice stop error:', error);
    }
  };
  
  const processVoiceCommand = (command) => {
    // Check for wake word
    const now = Date.now();
    const commandLower = command.toLowerCase();
    
    if (commandLower.includes(WAKE_WORD) || 
        (now - lastWakeWordTime.current) > wakeWordCooldown) {
      
      // Remove wake word if present
      let cleanCommand = command;
      if (commandLower.includes(WAKE_WORD)) {
        cleanCommand = command.replace(new RegExp(WAKE_WORD, 'gi'), '').trim();
      }
      
      if (cleanCommand) {
        addMessage('user', cleanCommand);
        sendToBackend(cleanCommand);
        lastWakeWordTime.current = now;
      } else {
        // Only wake word was said
        addMessage('system', '👋 Yes? How can I help?');
        Vibration.vibrate(200);
      }
    } else {
      // Ignore - no wake word
      console.log('Ignored - no wake word');
    }
  };
  
  const sendToBackend = (text) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const message = {
        type: 'text',
        content: text,
        session_id: sessionId,
        timestamp: new Date().toISOString()
      };
      wsRef.current.send(JSON.stringify(message));
      setIsProcessing(true);
    } else {
      addMessage('error', 'Not connected to server');
      Alert.alert('Connection Error', 'Please check your internet connection');
    }
  };
  
  const sendTextCommand = () => {
    if (inputText.trim()) {
      addMessage('user', inputText);
      sendToBackend(inputText);
      setInputText('');
    }
  };
  
  const handleServerMessage = (message) => {
    switch (message.type) {
      case 'response':
        addMessage('assistant', message.content);
        if (message.processing_time_ms) {
          console.log(`Response time: ${message.processing_time_ms}ms`);
        }
        setIsProcessing(false);
        break;
        
      case 'error':
        addMessage('error', message.content);
        setIsProcessing(false);
        break;
        
      case 'pong':
        console.log('Heartbeat received');
        break;
        
      case 'approval_needed':
        handleApprovalNeeded(message);
        break;
        
      case 'task_status':
        handleTaskStatus(message);
        break;
        
      case 'file_ready':
        handleFileReady(message);
        break;
        
      case 'vad_status':
        setVadConfidence(message.confidence);
        break;
        
      default:
        console.log('Unknown message type:', message.type);
    }
  };
  
  const handlePushNotification = (notification) => {
    const { type, file_id, file_name, approval_id, action, body, title } = notification.data;
    
    // Show notification in app
    addMessage('notification', body || title);
    
    switch (type) {
      case 'file_ready':
        Alert.alert(
          'File Ready 📁',
          `Your file "${file_name}" is ready to download`,
          [
            { text: 'Later', style: 'cancel' },
            { text: 'Download', onPress: () => downloadFile(file_id, file_name) }
          ]
        );
        break;
        
      case 'approval_needed':
        setPendingApproval({
          id: approval_id,
          action: action,
          details: body
        });
        setIsApprovalModalVisible(true);
        Vibration.vibrate([500, 500, 500]);
        break;
        
      case 'task_completed':
        addMessage('assistant', `✅ Task completed: ${body}`);
        break;
        
      case 'error':
        addMessage('error', body);
        break;
        
      default:
        addMessage('assistant', body);
    }
  };
  
  const handleApprovalNeeded = (message) => {
    setPendingApproval({
      id: message.approval_id,
      action: message.action,
      details: message.details
    });
    setIsApprovalModalVisible(true);
    Vibration.vibrate([500, 500, 500]);
  };
  
  const handleTaskStatus = (message) => {
    if (message.status === 'completed') {
      addMessage('assistant', `✅ Task completed: ${message.result}`);
      setCurrentTaskId(null);
      setUploadProgress(null);
    } else if (message.status === 'failed') {
      addMessage('error', `Task failed: ${message.error}`);
      setCurrentTaskId(null);
      setUploadProgress(null);
    } else if (message.status === 'running') {
      setUploadProgress(message.progress || 50);
    }
  };
  
  const handleFileReady = (message) => {
    Alert.alert(
      'File Ready',
      `Download ${message.file_name}?`,
      [
        { text: 'Later', style: 'cancel' },
        { text: 'Download', onPress: () => downloadFile(message.file_id, message.file_name) }
      ]
    );
  };
  
  const sendApproval = (approved) => {
    if (wsRef.current && pendingApproval) {
      wsRef.current.send(JSON.stringify({
        type: 'command',
        content: `${approved ? 'approve' : 'reject'}:${pendingApproval.id}`,
        session_id: sessionId,
        timestamp: new Date().toISOString()
      }));
    }
    setIsApprovalModalVisible(false);
    setPendingApproval(null);
    
    if (approved) {
      addMessage('system', '✅ Action approved');
    } else {
      addMessage('system', '❌ Action rejected');
    }
  };
  
  const pickAndUploadFile = async () => {
    try {
      const result = await DocumentPicker.pick({
        type: [DocumentPicker.types.allFiles]
      });
      
      const file = result[0];
      addMessage('system', `📤 Uploading ${file.name}...`);
      
      const formData = new FormData();
      formData.append('session_id', sessionId);
      formData.append('file', {
        uri: file.uri,
        type: file.type || 'application/octet-stream',
        name: file.name
      });
      
      const response = await fetch(`${API_URL}/api/v1/upload`, {
        method: 'POST',
        body: formData,
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
      
      const data = await response.json();
      setCurrentTaskId(data.task_id);
      addMessage('system', `✅ File uploaded. Processing...`);
      
      // Poll for task status
      pollTaskStatus(data.task_id);
      
    } catch (error) {
      if (DocumentPicker.isCancel(error)) {
        console.log('User cancelled');
      } else {
        console.error('File pick error:', error);
        addMessage('error', 'Failed to upload file');
      }
    }
  };
  
  const pollTaskStatus = async (taskId) => {
    const interval = setInterval(async () => {
      try {
        const response = await fetch(`${API_URL}/api/v1/tasks/${taskId}/status`, {
          method: 'POST'
        });
        const status = await response.json();
        
        if (status.status === 'completed') {
          clearInterval(interval);
          addMessage('assistant', `📄 File processed: ${status.result?.processed_content || 'Ready'}`);
          setCurrentTaskId(null);
          setUploadProgress(null);
          listUserFiles(); // Refresh file list
        } else if (status.status === 'failed') {
          clearInterval(interval);
          addMessage('error', `Processing failed: ${status.error}`);
          setCurrentTaskId(null);
          setUploadProgress(null);
        }
      } catch (error) {
        console.error('Poll error:', error);
      }
    }, 2000);
  };
  
  const downloadFile = async (fileId, fileName) => {
    try {
      const downloadUrl = `${API_URL}/api/v1/download/${fileId}?session_id=${sessionId}`;
      const downloadPath = `${RNFS.DocumentDirectoryPath}/${fileName}`;
      
      addMessage('system', `📥 Downloading ${fileName}...`);
      
      const result = await RNFS.downloadFile({
        fromUrl: downloadUrl,
        toFile: downloadPath,
        progress: (res) => {
          const progressPercent = (res.bytesWritten / res.contentLength) * 100;
          setUploadProgress(Math.round(progressPercent));
        }
      }).promise;
      
      if (result.statusCode === 200) {
        addMessage('system', `✅ Downloaded: ${fileName}`);
        Alert.alert(
          'Download Complete',
          `File saved to ${downloadPath}`,
          [
            { text: 'OK' },
            { text: 'Open', onPress: () => openFile(downloadPath) }
          ]
        );
        setUploadProgress(null);
      } else {
        throw new Error('Download failed');
      }
    } catch (error) {
      console.error('Download error:', error);
      addMessage('error', `Failed to download: ${fileName}`);
    }
  };
  
  const openFile = (path) => {
    // This would require a library like react-native-share
    Alert.alert('Open File', `File at: ${path}`);
  };
  
  const listUserFiles = async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/files/list?session_id=${sessionId}`);
      const data = await response.json();
      setUserFiles(data.files || []);
    } catch (error) {
      console.error('List files error:', error);
    }
  };
  
  const onRefresh = async () => {
    setRefreshing(true);
    await listUserFiles();
    setRefreshing(false);
  };
  
  const addMessage = (role, content) => {
    const newMessage = {
      id: Date.now().toString(),
      role,
      content,
      timestamp: moment().format('HH:mm:ss')
    };
    setMessages(prev => [...prev, newMessage]);
    
    // Auto-scroll
    setTimeout(scrollToBottom, 100);
  };
  
  const clearMessages = () => {
    Alert.alert(
      'Clear Chat',
      'Are you sure you want to clear all messages?',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Clear', onPress: () => setMessages([]) }
      ]
    );
  };
  
  const cleanup = () => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    if (audioStreamRef.current) {
      clearInterval(audioStreamRef.current);
    }
    Voice.destroy();
    AudioRecord.stop();
  };
  
  const getMessageStyle = (role) => {
    switch (role) {
      case 'user':
        return styles.userMessage;
      case 'assistant':
        return styles.assistantMessage;
      case 'error':
        return styles.errorMessage;
      case 'system':
        return styles.systemMessage;
      case 'notification':
        return styles.notificationMessage;
      default:
        return styles.assistantMessage;
    }
  };
  
  return (
    <SafeAreaView style={[styles.container, isDarkMode && styles.darkContainer]}>
      <KeyboardAvoidingView 
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.keyboardView}
      >
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.headerLeft}>
            <Text style={[styles.title, isDarkMode && styles.darkText]}>OpenClaw</Text>
            <View style={[styles.statusDot, isConnected && styles.statusConnected]} />
          </View>
          <View style={styles.headerRight}>
            <TouchableOpacity onPress={listUserFiles} style={styles.headerButton}>
              <Text style={styles.headerButtonText}>📁</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={clearMessages} style={styles.headerButton}>
              <Text style={styles.headerButtonText}>🗑️</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setIsDarkMode(!isDarkMode)} style={styles.headerButton}>
              <Text style={styles.headerButtonText}>{isDarkMode ? '☀️' : '🌙'}</Text>
            </TouchableOpacity>
          </View>
        </View>
        
        {/* VAD Indicator */}
        {isListening && (
          <View style={styles.vadContainer}>
            <View style={styles.vadBar}>
              <View style={[styles.vadProgress, { width: `${vadConfidence * 100}%` }]} />
            </View>
            <Text style={styles.vadText}>
              {vadConfidence > 0.7 ? '🎤 Speaking...' : '🎧 Listening...'}
            </Text>
          </View>
        )}
        
        {/* Upload Progress */}
        {uploadProgress !== null && (
          <View style={styles.progressContainer}>
            <View style={styles.progressBar}>
              <View style={[styles.progressFill, { width: `${uploadProgress}%` }]} />
            </View>
            <Text style={styles.progressText}>{uploadProgress}%</Text>
          </View>
        )}
        
        {/* Messages */}
        <ScrollView
          ref={messagesEndRef}
          style={styles.messagesContainer}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
          }
        >
          {messages.map((message) => (
            <View key={message.id} style={[styles.messageWrapper, getMessageStyle(message.role)]}>
              <View style={styles.messageHeader}>
                <Text style={styles.messageRole}>
                  {message.role === 'user' ? 'You' : 
                   message.role === 'assistant' ? 'Assistant' :
                   message.role === 'error' ? 'Error' :
                   message.role === 'system' ? 'System' : 'Info'}
                </Text>
                <Text style={styles.messageTime}>{message.timestamp}</Text>
              </View>
              <Text style={[styles.messageText, isDarkMode && styles.darkText]}>
                {message.content}
              </Text>
            </View>
          ))}
          {isProcessing && (
            <View style={styles.typingIndicator}>
              <ActivityIndicator size="small" color="#4caf50" />
              <Text style={styles.typingText}>Assistant is thinking...</Text>
            </View>
          )}
        </ScrollView>
        
        {/* Input Area */}
        <View style={styles.inputContainer}>
          <TouchableOpacity
            style={[styles.voiceButton, isListening && styles.voiceButtonActive]}
            onPress={isListening ? stopVoiceDetection : startVoiceDetection}
          >
            <Text style={styles.voiceButtonText}>
              {isListening ? '🔴 Stop' : '🎤 Speak'}
            </Text>
          </TouchableOpacity>
          
          <TouchableOpacity
            style={styles.fileButton}
            onPress={pickAndUploadFile}
          >
            <Text style={styles.fileButtonText}>📎</Text>
          </TouchableOpacity>
          
          <TextInput
            style={[styles.textInput, isDarkMode && styles.darkInput]}
            placeholder="Type a command..."
            placeholderTextColor={isDarkMode ? '#888' : '#999'}
            value={inputText}
            onChangeText={setInputText}
            onSubmitEditing={sendTextCommand}
            editable={!isProcessing}
          />
          
          <TouchableOpacity
            style={[styles.sendButton, !inputText.trim() && styles.sendButtonDisabled]}
            onPress={sendTextCommand}
            disabled={!inputText.trim()}
          >
            <Text style={styles.sendButtonText}>📤</Text>
          </TouchableOpacity>
        </View>
        
        {/* File List Modal */}
        <Modal
          visible={showFileMenu}
          transparent={true}
          animationType="slide"
          onRequestClose={() => setShowFileMenu(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>Your Files</Text>
              <ScrollView style={styles.fileList}>
                {userFiles.length === 0 ? (
                  <Text style={styles.noFilesText}>No files uploaded yet</Text>
                ) : (
                  userFiles.map((file) => (
                    <TouchableOpacity
                      key={file.file_id}
                      style={styles.fileItem}
                      onPress={() => {
                        setShowFileMenu(false);
                        downloadFile(file.file_id, file.name);
                      }}
                    >
                      <Text style={styles.fileName}>{file.name}</Text>
                      <Text style={styles.fileSize}>
                        {(file.size / 1024).toFixed(2)} KB
                      </Text>
                      <Text style={styles.fileDate}>
                        {moment(file.upload_time).fromNow()}
                      </Text>
                    </TouchableOpacity>
                  ))
                )}
              </ScrollView>
              <TouchableOpacity
                style={styles.closeModalButton}
                onPress={() => setShowFileMenu(false)}
              >
                <Text style={styles.closeModalText}>Close</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
        
        {/* Approval Modal */}
        <Modal
          visible={isApprovalModalVisible}
          transparent={true}
          animationType="fade"
          onRequestClose={() => setIsApprovalModalVisible(false)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.approvalModal}>
              <Text style={styles.approvalTitle}>⚠️ Approval Required</Text>
              <Text style={styles.approvalAction}>{pendingApproval?.action}</Text>
              <Text style={styles.approvalDetails}>{pendingApproval?.details}</Text>
              <View style={styles.approvalButtons}>
                <TouchableOpacity
                  style={[styles.approvalButton, styles.rejectButton]}
                  onPress={() => sendApproval(false)}
                >
                  <Text style={styles.approvalButtonText}>Reject</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.approvalButton, styles.approveButton]}
                  onPress={() => sendApproval(true)}
                >
                  <Text style={styles.approvalButtonText}>Approve</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </Modal>
        
        {/* Wake Word Hint */}
        <View style={styles.wakeWordHint}>
          <Text style={styles.hintText}>
            Say "{WAKE_WORD}" to activate
          </Text>
          <View style={[styles.connectionBadge, isConnected && styles.connectedBadge]}>
            <Text style={styles.connectionText}>
              {isConnected ? '● Connected' : '○ Disconnected'}
            </Text>
          </View>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f5f5f5',
  },
  darkContainer: {
    backgroundColor: '#1a1a1a',
  },
  keyboardView: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 15,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  title: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#333',
    marginRight: 10,
  },
  darkText: {
    color: '#fff',
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#ff4444',
  },
  statusConnected: {
    backgroundColor: '#44ff44',
    shadowColor: '#44ff44',
    shadowRadius: 3,
    shadowOpacity: 0.8,
  },
  headerRight: {
    flexDirection: 'row',
  },
  headerButton: {
    marginLeft: 15,
    padding: 5,
  },
  headerButtonText: {
    fontSize: 20,
  },
  vadContainer: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#f0f0f0',
  },
  vadBar: {
    height: 4,
    backgroundColor: '#ddd',
    borderRadius: 2,
    overflow: 'hidden',
  },
  vadProgress: {
    height: '100%',
    backgroundColor: '#4caf50',
    borderRadius: 2,
  },
  vadText: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 5,
    color: '#666',
  },
  progressContainer: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#e3f2fd',
  },
  progressBar: {
    height: 6,
    backgroundColor: '#bbdef5',
    borderRadius: 3,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#2196f3',
    borderRadius: 3,
  },
  progressText: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 5,
    color: '#1976d2',
  },
  messagesContainer: {
    flex: 1,
    padding: 20,
  },
  messageWrapper: {
    marginBottom: 15,
    padding: 12,
    borderRadius: 12,
    maxWidth: '85%',
  },
  userMessage: {
    backgroundColor: '#e3f2fd',
    alignSelf: 'flex-end',
  },
  assistantMessage: {
    backgroundColor: '#fff',
    alignSelf: 'flex-start',
    borderWidth: 1,
    borderColor: '#e0e0e0',
  },
  errorMessage: {
    backgroundColor: '#ffebee',
    alignSelf: 'center',
    maxWidth: '90%',
  },
  systemMessage: {
    backgroundColor: '#f5f5f5',
    alignSelf: 'center',
    maxWidth: '90%',
    fontStyle: 'italic',
  },
  notificationMessage: {
    backgroundColor: '#fff3e0',
    alignSelf: 'center',
    maxWidth: '90%',
    borderLeftWidth: 3,
    borderLeftColor: '#ff9800',
  },
  messageHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 5,
  },
  messageRole: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#666',
    textTransform: 'capitalize',
  },
  messageTime: {
    fontSize: 9,
    color: '#999',
  },
  messageText: {
    fontSize: 16,
    color: '#333',
    lineHeight: 22,
  },
  typingIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 10,
    alignSelf: 'flex-start',
    backgroundColor: '#fff',
    borderRadius: 20,
    marginTop: 5,
  },
  typingText: {
    marginLeft: 10,
    color: '#666',
    fontSize: 14,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 15,
    paddingVertical: 10,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#e0e0e0',
  },
  voiceButton: {
    backgroundColor: '#4caf50',
    paddingVertical: 12,
    paddingHorizontal: 15,
    borderRadius: 25,
    marginRight: 10,
  },
  voiceButtonActive: {
    backgroundColor: '#ff9800',
  },
  voiceButtonText: {
    color: '#fff',
    fontWeight: 'bold',
    fontSize: 14,
  },
  fileButton: {
    backgroundColor: '#2196f3',
    padding: 12,
    borderRadius: 25,
    marginRight: 10,
    width: 45,
    alignItems: 'center',
  },
  fileButtonText: {
    color: '#fff',
    fontSize: 18,
  },
  textInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 25,
    paddingHorizontal: 15,
    paddingVertical: 10,
    fontSize: 16,
    backgroundColor: '#fff',
  },
  darkInput: {
    backgroundColor: '#333',
    borderColor: '#555',
    color: '#fff',
  },
  sendButton: {
    backgroundColor: '#4caf50',
    padding: 12,
    borderRadius: 25,
    marginLeft: 10,
    width: 45,
    alignItems: 'center',
  },
  sendButtonDisabled: {
    backgroundColor: '#ccc',
  },
  sendButtonText: {
    color: '#fff',
    fontSize: 18,
  },
  wakeWordHint: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 8,
    backgroundColor: '#fafafa',
    borderTopWidth: 1,
    borderTopColor: '#eee',
  },
  hintText: {
    fontSize: 11,
    color: '#999',
  },
  connectionBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 12,
    backgroundColor: '#eee',
  },
  connectedBadge: {
    backgroundColor: '#e8f5e9',
  },
  connectionText: {
    fontSize: 10,
    color: '#666',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  modalContent: {
    backgroundColor: '#fff',
    borderRadius: 15,
    padding: 20,
    width: width * 0.9,
    maxHeight: height * 0.8,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 15,
    textAlign: 'center',
  },
  fileList: {
    maxHeight: height * 0.6,
  },
  fileItem: {
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
  },
  fileName: {
    fontSize: 16,
    fontWeight: '500',
    marginBottom: 4,
  },
  fileSize: {
    fontSize: 12,
    color: '#666',
  },
  fileDate: {
    fontSize: 11,
    color: '#999',
    marginTop: 2,
  },
  noFilesText: {
    textAlign: 'center',
    color: '#999',
    padding: 20,
  },
  closeModalButton: {
    marginTop: 15,
    padding: 12,
    backgroundColor: '#2196f3',
    borderRadius: 8,
    alignItems: 'center',
  },
  closeModalText: {
    color: '#fff',
    fontWeight: 'bold',
  },
  approvalModal: {
    backgroundColor: '#fff',
    borderRadius: 15,
    padding: 20,
    width: width * 0.85,
  },
  approvalTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#ff9800',
    marginBottom: 15,
    textAlign: 'center',
  },
  approvalAction: {
    fontSize: 16,
    fontWeight: '500',
    marginBottom: 10,
    textAlign: 'center',
  },
  approvalDetails: {
    fontSize: 14,
    color: '#666',
    marginBottom: 20,
    textAlign: 'center',
  },
  approvalButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  approvalButton: {
    paddingVertical: 10,
    paddingHorizontal: 25,
    borderRadius: 8,
    minWidth: 100,
    alignItems: 'center',
  },
  approveButton: {
    backgroundColor: '#4caf50',
  },
  rejectButton: {
    backgroundColor: '#f44336',
  },
  approvalButtonText: {
    color: '#fff',
    fontWeight: 'bold',
    fontSize: 16,
  },
});

export default App;