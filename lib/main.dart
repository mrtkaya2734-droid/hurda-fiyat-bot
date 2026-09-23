import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_core/firebase_core.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  String initializationStatus = "Firebase başlatılıyor...";
  
  try {
    if (Firebase.apps.isEmpty) {
      await Firebase.initializeApp(
        options: const FirebaseOptions(
          apiKey: "AIzaSyCzuPE0kGv2_jwdqmriC_gZkFjindvnz8Y",
          appId: "1:697786936426:web:5d4023bf38a96452c8b213",
          messagingSenderId: "697786936426",
          projectId: "cevhersan-16ec9",
          storageBucket: "cevhersan-16ec9.appspot.com",
        ),
      );
    }
    initializationStatus = "Firebase başarıyla bağlandı!";
  } catch (e) {
    initializationStatus = "Firebase Başlatma Hatası:\n$e";
  }

  runApp(CevhersanExchangeApp(statusMessage: initializationStatus));
}

class CevhersanExchangeApp extends StatelessWidget {
  final String statusMessage;
  const CevhersanExchangeApp({super.key, required this.statusMessage});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Cevhersanexchange',
      theme: ThemeData(
        primarySwatch: Colors.blueGrey,
        scaffoldBackgroundColor: Colors.grey[100],
      ),
      home: FiyatListesiPage(statusMessage: statusMessage),
    );
  }
}

class FiyatListesiPage extends StatelessWidget {
  final String statusMessage;
  const FiyatListesiPage({super.key, required this.statusMessage});

  @override
  Widget build(BuildContext context) {
    // Eğer Firebase hatasız başladıysa normal akışa geçelim, hata varsa ekrana yazdıralım
    if (statusMessage.startsWith("Firebase Başlatma Hatası")) {
      return Scaffold(
        appBar: AppBar(
          title: const Text('Cevhersanexchange - Hata Ekranı'),
          backgroundColor: Colors.red[900],
        ),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(20.0),
            child: Text(
              statusMessage,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 16, color: Colors.red, fontWeight: FontWeight.bold),
            ),
          ),
        ),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text('Cevhersanexchange - Güncel Hurda Fiyatları'),
        centerTitle: true,
        backgroundColor: Colors.blueGrey[900],
        foregroundColor: Colors.white,
      ),
      body: StreamBuilder<QuerySnapshot>(
        stream: FirebaseFirestore.instance.collection('prices').snapshots(),
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }

          if (snapshot.hasError) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(20.0),
                child: Text(
                  'Firestore Veri Çekme Hatası:\n${snapshot.error}',
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 15, color: Colors.red),
                ),
              ),
            );
          }

          if (!snapshot.hasData || snapshot.data!.docs.isEmpty) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(20.0),
                child: Text(
                  'Henüz fiyat verisi eklenmedi.\nBotun çalışmasını bekleyin veya veritabanını kontrol edin.',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 16, color: Colors.grey[600]),
                ),
              ),
            );
          }

          final docs = snapshot.data!.docs;

          return ListView.builder(
            itemCount: docs.length,
            padding: const EdgeInsets.all(10),
            itemBuilder: (context, index) {
              final data = docs[index].data() as Map<String, dynamic>;
              final fabrikaAdi = data['fabrika'] ?? 'Bilinmeyen Fabrika';
              final fiyat = data['fiyat'] ?? 'Belirtilmemiş';
              final tarih = data['tarih'] ?? '';

              return Card(
                elevation: 3,
                margin: const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(10),
                ),
                child: ListTile(
                  leading: CircleAvatar(
                    backgroundColor: Colors.blueGrey[100],
                    child: const Icon(Icons.factory, color: Colors.blueGrey),
                  ),
                  title: Text(
                    fabrikaAdi,
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 16,
                    ),
                  ),
                  subtitle: Text('Güncelleme: $tarih', style: const TextStyle(fontSize: 12)),
                  trailing: Text(
                    fiyat,
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 15,
                      color: Colors.green,
                    ),
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }
}