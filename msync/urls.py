from django.urls import path
from gvsdsdk.msync import views

app_name = 'msync'

urlpatterns = [
    path('auth/token/', views.AuthTokenView.as_view(), name='auth_token'),
    path('auth/token/refresh/', views.AuthTokenRefreshView.as_view(), name='auth_token_refresh'),
    path('auth/introspect/', views.AuthTokenIntrospectView.as_view(), name='auth_introspect'),

    path('registry/', views.RegistryListView.as_view(), name='registry_list'),
    path('registry/<str:name>/', views.RegistryDetailView.as_view(), name='registry_detail'),
    path('registry/<str:name>/heartbeat/', views.RegistryHeartbeatView.as_view(), name='registry_heartbeat'),
    path('registry/<str:name>/deregister/', views.RegistryDeregisterView.as_view(), name='registry_deregister'),

    path('ogm/', views.OGMRegisterView.as_view(), name='ogm_register'),
    path('ogm/batch/', views.OGMBatchRegisterView.as_view(), name='ogm_batch_register'),
    path('ogm/query/', views.OGMQueryView.as_view(), name='ogm_query'),
    path('ogm/<str:global_id>/', views.OGMResolveView.as_view(), name='ogm_resolve'),
    path('ogm/<str:global_id>/update/', views.OGMUpdateView.as_view(), name='ogm_update'),
    path('ogm/<str:global_id>/deregister/', views.OGMDeregisterView.as_view(), name='ogm_deregister'),

    path('tx/', views.TransactionCreateView.as_view(), name='tx_create'),
    path('tx/query/', views.TransactionQueryView.as_view(), name='tx_query'),
    path('tx/timeout-check/', views.TransactionTimeoutCheckView.as_view(), name='tx_timeout_check'),
    path('tx/<str:transaction_code>/', views.TransactionDetailView.as_view(), name='tx_detail'),
    path('tx/<str:transaction_code>/execute/', views.TransactionExecuteView.as_view(), name='tx_execute'),
    path('tx/<str:transaction_code>/compensate/', views.TransactionCompensateView.as_view(), name='tx_compensate'),
    path('tx/<str:transaction_code>/cancel/', views.TransactionCancelView.as_view(), name='tx_cancel'),
    path('tx/<str:transaction_code>/retry/', views.TransactionRetryStepView.as_view(), name='tx_retry_step'),
    path('tx/<str:transaction_code>/retry-compensate/', views.TransactionRetryCompensateView.as_view(), name='tx_retry_compensate'),

    path('local/execute/', views.LocalExecuteView.as_view(), name='local_execute'),
    path('local/compensate/', views.LocalCompensateView.as_view(), name='local_compensate'),
    path('local/handlers/', views.LocalHandlersView.as_view(), name='local_handlers'),
]
