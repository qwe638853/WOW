<template>
    <div class="dashboard-container">
      <header class="dashboard-header">
        <h2>🏥 醫院儀表板</h2>
        <p>歡迎，{{ username }}！請上傳健康檢查數據</p>
        <button class="logout-btn" @click="$emit('logout')">登出</button>
      </header>
      <section class="upload-section card">
        <UploadData />
      </section>
    </div>
  </template>
  
  <script setup>
  import UploadData from './components/UploadData.vue';
  
  defineProps({
    username: {
      type: String,
      default: '醫院員工'
    }
  });
  
  defineEmits(['logout']);
  </script>
  
  <style scoped>
  .dashboard-container {
    max-width: 1200px; /* 適合桌面端 */
    margin: 40px auto;
    padding: 20px;
  }
  
  .dashboard-header {
    text-align: center;
    margin-bottom: 30px;
  }
  
  .dashboard-header h2 {
    font-size: 32px;
    margin-bottom: 10px;
  }
  
  .dashboard-header p {
    color: var(--muted-color);
    font-size: 18px;
  }
  
  .logout-btn {
    margin-top: 15px;
    padding: 10px 20px;
    background-color: #dc3545;
    color: var(--white);
    border: none;
    border-radius: var(--border-radius);
    font-size: 16px;
  }
  
  .card {
    background: var(--white);
    padding: 30px;
    border-radius: var(--border-radius);
    box-shadow: var(--shadow);
    transition: transform 0.3s ease;
  }
  
  .card:hover {
    transform: translateY(-5px);
  }
  
  .upload-section {
    text-align: center;
  }
  </style>