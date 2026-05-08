import 'package:flutter/material.dart';

import 'features/autopositioning_page.dart';
import 'features/connection_page.dart';
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

class BioSpurHomeShell extends StatefulWidget {
  const BioSpurHomeShell({super.key});

  @override
  State<BioSpurHomeShell> createState() => _BioSpurHomeShellState();
}

class _BioSpurHomeShellState extends State<BioSpurHomeShell> {
  int _selectedIndex = 0;

  static const _titles = <String>[
    'BioSpur UWB - AutoPos',
    'BioSpur UWB - Dashboard',
    'BioSpur UWB - Sessions',
    'BioSpur UWB - Live View',
    'BioSpur UWB - 3D View',
    'BioSpur UWB - Legacy BLE',
  ];

  Widget _buildCurrentPage() {
    switch (_selectedIndex) {
      case 0:
        return const AutopositioningPage();
      case 1:
        return const DashboardPage();
      case 2:
        return const SessionsPage();
      case 3:
        return const LiveViewPage();
      case 4:
        return const ThreeDViewPage();
      case 5:
        return const ConnectionPage();
      default:
        return const AutopositioningPage();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_selectedIndex]),
      ),
      body: _buildCurrentPage(),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex,
        onDestinationSelected: (index) {
          setState(() {
            _selectedIndex = index;
          });
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.hub_outlined),
            label: 'AutoPos',
          ),
          NavigationDestination(
            icon: Icon(Icons.dashboard_outlined),
            label: 'Dashboard',
          ),
          NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            label: 'Sessions',
          ),
          NavigationDestination(
            icon: Icon(Icons.monitor_heart_outlined),
            label: 'Live',
          ),
          NavigationDestination(
            icon: Icon(Icons.view_in_ar_outlined),
            label: '3D',
          ),
          NavigationDestination(
            icon: Icon(Icons.build_circle_outlined),
            label: 'Legacy BLE',
          ),
        ],
      ),
    );
  }
}
