import 'package:flutter/material.dart';

import 'features/autopositioning_page.dart';
import 'features/dashboard_page.dart';
import 'features/live_view_page.dart';
import 'features/sessions_page.dart';
import 'features/three_d_view_page.dart';

class BioSpurApp extends StatelessWidget {
  const BioSpurApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'BioSpur UWB',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF175CD3),
          brightness: Brightness.light,
        ),
      ),
      home: const BioSpurHomeShell(),
    );
  }
}

class BioSpurHomeShell extends StatelessWidget {
  const BioSpurHomeShell({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 5,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('BioSpur UWB'),
          bottom: const TabBar(
            isScrollable: true,
            tabs: [
              Tab(text: 'Dashboard'),
              Tab(text: 'Live View'),
              Tab(text: 'Sessions'),
              Tab(text: '3D View'),
              Tab(text: 'Autopositioning'),
            ],
          ),
        ),
        body: const TabBarView(
          children: [
            DashboardPage(),
            LiveViewPage(),
            SessionsPage(),
            ThreeDViewPage(),
            AutopositioningPage(),
          ],
        ),
      ),
    );
  }
}
